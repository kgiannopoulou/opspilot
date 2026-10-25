"""The grounding check: what makes a write-up acceptable to show an engineer during an outage."""

from app.ai import grounding
from app.ai.engine import Evidence, InvestigationResult
from app.ai.writer import LLMWriter
from app.models import Incident
from app.models.enums import EvidenceKind, EvidenceStance

LOG = Evidence(
    EvidenceKind.LOG_PATTERN,
    EvidenceStance.SUPPORTS,
    "31 GatewayTimeoutException events from payment-service between 14:27 and 14:34 UTC",
    0.45,
    {"count": 31, "sample": {"timeout": "5000ms", "provider": "stripe"}},
    ref="LOG-48392",
)
LATENCY = Evidence(
    EvidenceKind.METRIC_DELTA,
    EvidenceStance.SUPPORTS,
    "payment-service p95 latency increased 412% (1,038 ms → 5,321 ms)",
    0.3,
    {"baseline": 1038.2, "current": 5321.4, "change_pct": 412.6},
    ref="METRIC-payment-service-latency",
)
RUNBOOK = Evidence(
    EvidenceKind.DOCUMENT,
    EvidenceStance.CONTEXT,
    "Payment Gateway Timeout Runbook §1 Triage",
    ref="RUNBOOK-payment-gateway-timeout §1",
)
EVIDENCE = [LOG, LATENCY, RUNBOOK]
ROOT_CAUSE = "Gateway calls exceed the 5000ms client timeout."


def test_well_cited_narrative_passes():
    narrative = (
        "payment-service recorded 31 GatewayTimeoutException events between 14:27 and 14:34 UTC [LOG-48392]. "
        "p95 latency rose from 1,038 ms to 5,321 ms, an increase of 412% [METRIC-payment-service-latency]. "
        "The runbook's triage section applies [RUNBOOK-payment-gateway-timeout §1, LOG-48392]."
    )
    report = grounding.check(narrative, ROOT_CAUSE, EVIDENCE)
    assert report.passed, report
    assert report.cited == ["LOG-48392", "METRIC-payment-service-latency", "RUNBOOK-payment-gateway-timeout §1"]


def test_invented_reference_fails():
    report = grounding.check("The gateway timed out 31 times [LOG-99999].", ROOT_CAUSE, EVIDENCE)
    assert not report.passed and report.unknown_refs == ["LOG-99999"]


def test_uncited_claim_fails():
    report = grounding.check("Payments time out [LOG-48392]. The provider is down.", ROOT_CAUSE, EVIDENCE)
    assert report.uncited_sentences == ["The provider is down."]


def test_numbers_must_appear_in_the_cited_evidence():
    # 5.3 s is a fair paraphrase of 5,321 ms, but the reader cannot find it where the citation points.
    report = grounding.check("Latency reached 5.3 s [METRIC-payment-service-latency].", ROOT_CAUSE, EVIDENCE)
    assert report.unsupported_numbers == ["5.3 in: Latency reached 5.3 s."]
    # The count is in the log evidence, not in the metric that is cited.
    report = grounding.check("31 requests were slow [METRIC-payment-service-latency].", ROOT_CAUSE, EVIDENCE)
    assert not report.passed
    # Numbers in the root cause must exist somewhere in the evidence.
    report = grounding.check("Timeouts [LOG-48392].", "Gateway calls exceed the 8000ms timeout.", EVIDENCE)
    assert report.unsupported_numbers == ["8000 in root cause"]


def test_incident_context_numbers_are_allowed():
    report = grounding.check("INC-421 is caused by timeouts [LOG-48392].", ROOT_CAUSE, EVIDENCE, "INC-421 Checkout")
    assert report.passed


class HallucinatingLLM:
    name = "fake-model"

    def complete_json(self, system, prompt, schema, max_tokens=4096):
        return {
            "root_cause": "The stripe gateway is down.",
            "narrative": "Stripe reported a regional outage at 14:20 [STATUS-stripe].",
            "recommended_steps": ["Wait"],
        }


def test_llm_draft_failing_the_check_is_replaced_by_the_extractive_one():
    incident = Incident(number="INC-421", title="Payment checkout timeout", summary="")
    result = InvestigationResult(
        hypothesis_key="payment_gateway_timeout",
        root_cause=ROOT_CAUSE,
        confidence=0.8,
        recommended_steps=["Check the provider status page"],
        suggested_sql=None,
        evidence=EVIDENCE,
        window_start=None,
        window_end=None,
    )
    draft = LLMWriter(HallucinatingLLM()).write(incident, result)

    assert draft.writer == "extractive-v1" and draft.root_cause == ROOT_CAUSE
    assert draft.grounding["passed"] is True
    rejected = draft.grounding["rejected_llm_draft"]
    assert rejected["unknown_refs"] == ["STATUS-stripe"] and "20 in: Stripe" in rejected["unsupported_numbers"][0]
    assert draft.narrative.startswith("31 GatewayTimeoutException events") and "[LOG-48392]" in draft.narrative
