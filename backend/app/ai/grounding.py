"""Check a write-up against the evidence it cites before anyone sees it.

A write-up cites facts with markers such as ``[LOG-48392]`` or ``[METRIC-payment-service-latency, INC-106]``.
Three rules, all mechanical, so they apply equally to an LLM draft and to the extractive one:

1. **Citations resolve** — every marker names an evidence item of this investigation. A made-up reference is the
   most direct sign of a hallucination.
2. **Claims are cited** — every sentence of the narrative carries at least one marker.
3. **Numbers are grounded** — every number in a cited sentence appears in the evidence that sentence cites
   (summary or raw data). "Latency rose to 5.3 s" fails when the evidence says 5,321 ms: the reader must be able to
   find the number where the citation points. Numbers in the root cause must appear somewhere in the evidence.

The report is stored with the investigation; a draft that fails is not shown (the extractive write-up is used and
the failure is recorded, which is itself useful when evaluating models and prompts).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.engine import Evidence

CITATION = re.compile(r"\[([A-Z][A-Z]*-[^\[\]]+?)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=\S)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class GroundingReport:
    passed: bool
    cited: list[str] = field(default_factory=list)
    unknown_refs: list[str] = field(default_factory=list)
    uncited_sentences: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def citations(text: str) -> list[str]:
    """All refs cited in `text`, in order of appearance; `[A, B]` cites both A and B."""
    refs: list[str] = []
    for group in CITATION.findall(text):
        refs.extend(r.strip() for r in group.split(",") if r.strip())
    return refs


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]


def strip_citations(text: str) -> str:
    return re.sub(r"\s*" + CITATION.pattern, "", text)


def _numbers(text: str) -> set[str]:
    """Numbers as written, normalised: thousands separators and trailing zeros removed (5,321 → 5321; 0.50 → 0.5)."""
    out = set()
    for raw in _NUMBER.findall(text):
        n = raw.replace(",", "")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n)
    return out


def _evidence_text(e: Evidence) -> str:
    return f"{e.summary} {json.dumps(e.data, default=str)}"


def check(narrative: str, root_cause: str, evidence: list[Evidence], allowed_text: str = "") -> GroundingReport:
    """`allowed_text` holds context the reader can see anyway (incident number and title): its numbers are fine."""
    by_ref = {e.ref: e for e in evidence if e.ref}
    allowed_numbers = _numbers(allowed_text)
    report = GroundingReport(passed=True)

    for sentence in split_sentences(narrative):
        refs = citations(sentence)
        if not refs:
            report.uncited_sentences.append(sentence)
            continue
        known = [by_ref[r] for r in refs if r in by_ref]
        report.unknown_refs.extend(r for r in refs if r not in by_ref)
        report.cited.extend(r for r in refs if r in by_ref and r not in report.cited)
        # Section numbers inside the markers ("§3.2") are not claims.
        cited_numbers = set().union(*(_numbers(_evidence_text(e)) for e in known)) if known else set()
        for n in sorted(_numbers(strip_citations(sentence)) - cited_numbers - allowed_numbers):
            report.unsupported_numbers.append(f"{n} in: {strip_citations(sentence)[:120]}")

    all_numbers = set().union(*(_numbers(_evidence_text(e)) for e in evidence)) if evidence else set()
    for n in sorted(_numbers(strip_citations(root_cause)) - all_numbers - allowed_numbers):
        report.unsupported_numbers.append(f"{n} in root cause")
    report.unknown_refs.extend(r for r in citations(root_cause) if r not in by_ref)

    report.passed = not (report.unknown_refs or report.uncited_sentences or report.unsupported_numbers)
    return report
