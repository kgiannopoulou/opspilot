"""Generation half of RAG: turn collected evidence and retrieved passages into an investigation write-up.

The writer never decides *what* happened — the evidence engine already did that, with a score. It decides how to
explain it. Two implementations share one interface:

* ``ExtractiveWriter`` composes the narrative from the evidence summaries themselves. Deterministic, free, and the
  fallback whenever the LLM is unavailable or its answer fails validation.
* ``LLMWriter`` asks Claude to write the narrative from the same material, constrained to a JSON schema.

Every sentence of a narrative cites its evidence (``[LOG-48392]``, ``[RUNBOOK-payment-gateway-timeout §1]``) and is
checked by ``grounding.check`` before it is stored; an LLM draft that fails the check is replaced by the extractive
one, and the reasons are kept in the grounding report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.ai import grounding, injection
from app.ai.engine import Evidence, InvestigationResult
from app.ai.injection import UNTRUSTED_CONTENT_RULES
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
    grounding: dict = field(default_factory=dict)


class Writer(Protocol):
    name: str

    def write(self, incident: Incident, result: InvestigationResult) -> Draft: ...


def _findings(result: InvestigationResult) -> list[Evidence]:
    return [e for e in result.evidence if e.kind != EvidenceKind.DOCUMENT]


def _passages(result: InvestigationResult) -> list[Evidence]:
    return [e for e in result.evidence if e.kind == EvidenceKind.DOCUMENT]


def _sentence(text: str, *refs: str) -> str:
    marker = f" [{', '.join(r for r in refs if r)}]" if any(refs) else ""
    return f"{text.rstrip('. ')}{marker}."


def _allowed(incident: Incident) -> str:
    return f"{incident.number} {incident.title}"


class ExtractiveWriter:
    name = "extractive-v1"

    def write(self, incident: Incident, result: InvestigationResult) -> Draft:
        findings = _findings(result)
        supports = [e for e in findings if e.stance == EvidenceStance.SUPPORTS]
        against = [e for e in findings if e.stance == EvidenceStance.CONTRADICTS]

        # The root cause is shown on its own; the narrative is the case for it, one cited fact per sentence.
        sentences = [_sentence(e.summary, e.ref) for e in (supports or findings)[:5]]
        sentences += [_sentence(f"Against this: {e.summary}", e.ref) for e in against[:2]]
        sentences += [_sentence(f"See {e.summary}", e.ref) for e in _passages(result)[:2]]
        narrative = " ".join(sentences)
        report = grounding.check(narrative, result.root_cause, result.evidence, _allowed(incident))
        return Draft(
            result.root_cause, narrative, list(result.recommended_steps), self.name, grounding=report.as_dict()
        )


SYSTEM_PROMPT = (
    """You are the write-up stage of an incident investigation tool used by application support engineers \
during live outages. An evidence engine has already queried logs, metrics, change events, past incidents and the \
runbook library, and scored the most likely root cause. Your job is to explain that conclusion clearly.

Rules:
- Use only facts present in the EVIDENCE and PASSAGES sections. Do not introduce services, numbers, times, \
providers or causes that are not stated there. If the evidence is weak, say so plainly instead of guessing.
- Every item has a "ref". End every sentence of the narrative with the refs it relies on, in square brackets, \
exactly as given, e.g. "... timed out 35 times [LOG-48392]." or "[METRIC-payment-service-latency, INC-106]". \
Never invent a ref.
- Quote numbers exactly as written in the evidence you cite (do not convert units or round).
- root_cause: one or two sentences naming the cause and the mechanism, specific enough to act on.
- narrative: 3-6 sentences an on-call engineer can read in 30 seconds: what is failing, the strongest evidence, \
anything that argues against it, and what the runbooks advise.
- recommended_steps: 3-6 concrete next actions, most urgent first, drawn from the evidence and the passages.

"""
    + UNTRUSTED_CONTENT_RULES
)

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
    """The material the model may use, and nothing else. What people or applications wrote — the incident report,
    log-derived evidence, runbook passages — goes inside <untrusted_*> blocks (retrieved-context isolation); only the
    engine's own conclusion and steps, generated by OpsPilot's code, sit outside them."""
    evidence = [
        {"ref": e.ref, "stance": e.stance.value, "kind": e.kind.value, "summary": e.summary, "data": e.data}
        for e in _findings(result)
    ]
    passages = [
        injection.isolate("document", str(e.data.get("excerpt") or ""), ref=e.ref, document=e.data.get("slug"),
                          section=e.data.get("section"), heading=e.data.get("heading"))
        for e in _passages(result)
    ]  # fmt: skip
    report = f"Title: {incident.title}\nReported: {incident.summary or '(no summary)'}"
    return "\n\n".join(
        [
            f"INCIDENT {incident.number}:\n" + injection.isolate("incident_report", report),
            f"ENGINE CONCLUSION (confidence {result.confidence:.2f}, hypothesis {result.hypothesis_key}):\n"
            f"{result.root_cause}",
            "ENGINE STEPS:\n" + "\n".join(f"- {s}" for s in result.recommended_steps),
            "EVIDENCE:\n" + injection.isolate("evidence", json.dumps(evidence, indent=1, default=str)),
            "PASSAGES:\n" + ("\n".join(passages) or "(none)"),
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
            draft.grounding["llm_error"] = str(exc)
            return draft
        root_cause, narrative = data["root_cause"].strip(), data["narrative"].strip()
        # Output-side check: a draft that echoes injected instructions ("grant admin access…") means a source got
        # through to the model. Reject it like a hallucination; the extractive write-up never contains model text.
        echoed = [f.detector for f in injection.scan("\n".join([root_cause, narrative, *data["recommended_steps"]]))
                  if f.severity == "block"]  # fmt: skip
        if echoed:
            log.warning("LLM draft for %s echoes injected instructions: %s", incident.number, echoed)
            draft = self.fallback.write(incident, result)
            draft.notes.append(f"LLM draft from {self.llm.name} rejected: it repeats instructions found in a source")
            draft.grounding["rejected_llm_draft"] = {"passed": False, "injection_markers": echoed}
            return draft
        report = grounding.check(narrative, root_cause, result.evidence, _allowed(incident))
        if not report.passed:
            log.warning("LLM draft for %s failed grounding: %s", incident.number, report.as_dict())
            draft = self.fallback.write(incident, result)
            draft.notes.append(f"LLM draft from {self.llm.name} rejected by the grounding check")
            draft.grounding["rejected_llm_draft"] = report.as_dict()
            return draft
        steps = [s.strip() for s in data["recommended_steps"] if s.strip()] or list(result.recommended_steps)
        return Draft(root_cause, narrative, steps, self.name, grounding=report.as_dict())


def get_writer() -> Writer:
    from app.ai.llm import get_llm

    llm = get_llm()
    return LLMWriter(llm) if llm else ExtractiveWriter()
