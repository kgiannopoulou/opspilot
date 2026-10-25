"""Generation half of RAG: turn collected evidence and retrieved passages into an investigation write-up.

The writer never decides *what* happened — the evidence engine already did that, with a score. It decides how to
explain it. Two implementations share one interface:

* ``ExtractiveWriter`` composes the narrative from the evidence summaries themselves. Deterministic, free, and the
  fallback whenever the LLM is unavailable or its answer fails validation.
* ``LLMWriter`` asks Claude to write the narrative from the same material, constrained to a JSON schema.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.ai.engine import Evidence, InvestigationResult
from app.ai.llm import LLMClient, LLMError
from app.models import Incident
from app.models.enums import EvidenceKind, EvidenceStance

log = logging.getLogger(__name__)


@dataclass
class Draft:
    root_cause: str
    narrative: str
    recommended_steps: list[str]
    writer: str
    notes: list[str] = field(default_factory=list)  # e.g. why the LLM draft was rejected


class Writer(Protocol):
    name: str

    def write(self, incident: Incident, result: InvestigationResult) -> Draft: ...


def _findings(result: InvestigationResult) -> list[Evidence]:
    return [e for e in result.evidence if e.kind != EvidenceKind.DOCUMENT]


def _passages(result: InvestigationResult) -> list[Evidence]:
    return [e for e in result.evidence if e.kind == EvidenceKind.DOCUMENT]


class ExtractiveWriter:
    name = "extractive-v1"

    def write(self, incident: Incident, result: InvestigationResult) -> Draft:
        supports = [e for e in _findings(result) if e.stance == EvidenceStance.SUPPORTS]
        against = [e for e in _findings(result) if e.stance == EvidenceStance.CONTRADICTS]
        parts = [result.root_cause]
        if supports:
            parts.append("Supporting evidence: " + "; ".join(e.summary for e in supports[:4]) + ".")
        if against:
            parts.append("Against this: " + "; ".join(e.summary for e in against[:2]) + ".")
        if passages := _passages(result):
            parts.append("Guidance: " + "; ".join(e.summary for e in passages[:2]) + ".")
        return Draft(result.root_cause, " ".join(parts), list(result.recommended_steps), self.name)


SYSTEM_PROMPT = """You are the write-up stage of an incident investigation tool used by application support engineers \
during live outages. An evidence engine has already queried logs, metrics, change events, past incidents and the \
runbook library, and scored the most likely root cause. Your job is to explain that conclusion clearly.

Rules:
- Use only facts present in the EVIDENCE and PASSAGES sections. Do not introduce services, numbers, times, \
providers or causes that are not stated there. If the evidence is weak, say so plainly instead of guessing.
- Quote numbers exactly as they appear in the evidence.
- root_cause: one or two sentences naming the cause and the mechanism, specific enough to act on.
- narrative: 3-6 sentences an on-call engineer can read in 30 seconds: what is failing, the strongest evidence, \
anything that argues against it, and what the runbooks advise.
- recommended_steps: 3-6 concrete next actions, most urgent first, drawn from the evidence and the passages."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "narrative": {"type": "string"},
        "recommended_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["root_cause", "narrative", "recommended_steps"],
    "additionalProperties": False,
}


def render_prompt(incident: Incident, result: InvestigationResult) -> str:
    """The material the model may use, and nothing else."""
    evidence = [
        {"stance": e.stance.value, "kind": e.kind.value, "summary": e.summary, "data": e.data}
        for e in _findings(result)
    ]
    passages = [
        {"document": e.data.get("slug"), "section": e.data.get("section"), "heading": e.data.get("heading"),
         "text": e.data.get("excerpt")}
        for e in _passages(result)
    ]  # fmt: skip
    return "\n\n".join(
        [
            f"INCIDENT {incident.number}: {incident.title}\nReported: {incident.summary or '(no summary)'}",
            f"ENGINE CONCLUSION (confidence {result.confidence:.2f}, hypothesis {result.hypothesis_key}):\n"
            f"{result.root_cause}",
            "ENGINE STEPS:\n" + "\n".join(f"- {s}" for s in result.recommended_steps),
            "EVIDENCE:\n" + json.dumps(evidence, indent=1, default=str),
            "PASSAGES:\n" + json.dumps(passages, indent=1),
        ]
    )


class LLMWriter:
    def __init__(self, llm: LLMClient, fallback: Writer | None = None) -> None:
        self.llm = llm
        self.fallback = fallback or ExtractiveWriter()
        self.name = f"claude/{llm.name}"

    def write(self, incident: Incident, result: InvestigationResult) -> Draft:
        try:
            data = self.llm.complete_json(SYSTEM_PROMPT, render_prompt(incident, result), DRAFT_SCHEMA)
        except LLMError as exc:
            log.warning("LLM writer unavailable for %s: %s", incident.number, exc)
            draft = self.fallback.write(incident, result)
            draft.notes.append(f"LLM unavailable ({exc}); extractive write-up used")
            return draft
        steps = [s.strip() for s in data["recommended_steps"] if s.strip()] or list(result.recommended_steps)
        return Draft(data["root_cause"].strip(), data["narrative"].strip(), steps, self.name)


def get_writer() -> Writer:
    from app.ai.llm import get_llm

    llm = get_llm()
    return LLMWriter(llm) if llm else ExtractiveWriter()
