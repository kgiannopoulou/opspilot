"""Ticket triage: predict category, priority and affected service from what the customer wrote.

The output is a *suggestion* (stored as a PENDING ticket_classifications row); nothing changes on the ticket until
an analyst accepts or corrects it. Two engines share one interface:

* ``KeywordClassifier`` (``keywords-v1``) — transparent rules: category lexicons, impact phrases for priority,
  service mentions, and the knowledge base as a tie-breaker (the top passage for the ticket text names a service).
  Every prediction lists the words it was based on, so a wrong one is easy to understand.
* ``LLMClassifier`` — Claude with a JSON schema whose enums are the real categories, priorities and service names,
  so it cannot answer outside the catalogue. Falls back to the keyword engine on any failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.ai.embeddings import stem, tokenize
from app.ai.llm import LLMClient, LLMError
from app.ai.retrieval import KnowledgeRetriever
from app.models import KnowledgeDocument, Service
from app.models.enums import Priority, TicketCategory

log = logging.getLogger(__name__)
C = TicketCategory

LEXICON: dict[TicketCategory, tuple[str, ...]] = {
    C.PAYMENTS: ("payment", "pay", "checkout", "card", "refund", "stripe", "gateway", "charge", "payout", "declined"),
    C.AUTHENTICATION: ("login", "logout", "password", "reset", "mfa", "session", "signin", "account", "locked"),
    C.ORDERS: ("order", "export", "csv", "invoice", "vat", "cart", "inventory", "shipment", "refund"),
    C.NOTIFICATIONS: ("email", "mail", "smtp", "webhook", "notification", "receipt", "subject"),
    C.DATABASE: ("database", "query", "connection", "postgres", "deadlock", "replica", "queuepool"),
    C.PERFORMANCE: ("slow", "latency", "load", "loading", "spinner", "seconds", "timeout", "sluggish", "lag"),
}
# Words that hint at a category but are too common to decide it alone ("order" appears in most ticket types).
GENERIC = {"order", "account", "load", "second", "pay"}
KB_MIN_SCORE = 0.3
CATEGORY_SERVICE = {
    C.PAYMENTS: "payment-service",
    C.AUTHENTICATION: "auth-service",
    C.ORDERS: "order-service",
    C.NOTIFICATIONS: "notification-service",
    C.DATABASE: "postgres-primary",
}
# Product areas customers name that map to a service without naming it.
SERVICE_HINTS = {"dashboard": "order-service", "webhook": "notification-service", "invoice": "order-service"}

FAILING = re.compile(
    r"\b(cannot|can't|unable|fail\w*|error\w*|freez\w*|frozen|down|outage|broken|times? out|timing out|500|crash\w*)\b",
    re.I,
)
BREADTH = re.compile(
    r"\b(several|many|multiple|all|every|everyone|customers|merchants|users"
    r"|\d+ (?:of|chats|merchants|customers|orders))\b",
    re.I,
)
DEGRADED = re.compile(r"\b(slow\w*|delay\w*|latency|intermittent\w*|sometimes|spinner|seconds|takes)\b", re.I)
COSMETIC = re.compile(r"\b(typo|wording|question|how do i|logo|colou?r|cosmetic|spelling)\b", re.I)
LOGIN = re.compile(r"\b(log ?in|sign ?in|logged out|session)\b", re.I)


@dataclass
class Prediction:
    category: TicketCategory
    priority: Priority
    service: str | None
    confidence: float
    rationale: str
    engine: str
    signals: dict[str, Any] = field(default_factory=dict)


class Classifier(Protocol):
    name: str

    def classify(self, title: str, description: str) -> Prediction: ...


def _stems(words: tuple[str, ...]) -> set[str]:
    # Stemmed like the ticket text, but without synonym expansion: "login" must not also count as "auth".
    return {stem(w.lower()) for w in words}


class KeywordClassifier:
    name = "keywords-v1"

    def __init__(self, db: Session | None = None, services: list[str] | None = None) -> None:
        self.db = db
        self.services = services if services is not None else ([s.name for s in db.query(Service)] if db else [])
        self.lexicon = {cat: _stems(words) for cat, words in LEXICON.items()}

    def _priority(self, text: str, category: TicketCategory) -> tuple[Priority, list[str]]:
        failing, breadth = FAILING.findall(text), BREADTH.findall(text)
        degraded, cosmetic = DEGRADED.findall(text), COSMETIC.findall(text)
        # Core flows (incident process, SEV1): checkout/payments and login — not password reset or dashboards.
        core = category == C.PAYMENTS or (category == C.AUTHENTICATION and bool(LOGIN.search(text)))
        reasons = [f"{name}: {', '.join(sorted(set(found)))}"
                   for name, found in (("failing", failing), ("breadth", breadth), ("degraded", degraded),
                                       ("cosmetic", cosmetic)) if found]  # fmt: skip
        if core:
            reasons.append(f"core flow: {category.value.lower()}")
        if cosmetic and not failing:
            return Priority.P4, reasons
        if failing and core and breadth:
            return Priority.P1, reasons
        if failing and (core or breadth) or degraded and breadth:
            return Priority.P2, reasons
        return Priority.P3, reasons

    def _kb_service(self, text: str) -> tuple[str | None, str | None]:
        """The service of the best-matching knowledge-base document: the same retrieval investigations use."""
        if self.db is None:
            return None, None
        for passage in KnowledgeRetriever(self.db).search(text, k=3):
            if passage.score < KB_MIN_SCORE:
                break
            doc = self.db.get(KnowledgeDocument, passage.document_id)
            if doc is not None and doc.service is not None:
                return doc.service.name, f"{passage.title} §{passage.section}"
        return None, None

    def classify(self, title: str, description: str) -> Prediction:
        text = f"{title}. {description}"
        tokens = tokenize(text)
        hits = {cat: sorted({t for t in tokens if t in stems}) for cat, stems in self.lexicon.items()}
        title_tokens = set(tokenize(title))
        scores = {cat: sum(0.5 if w in GENERIC else 1 for w in words) + (0.5 if title_tokens & set(words) else 0)
                  for cat, words in hits.items()}  # fmt: skip
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], list(C).index(kv[0])))
        (top, top_score), (_, second_score) = ranked[0], ranked[1]
        category = top if top_score > 0 else C.OTHER

        # A purely performance-flavoured ticket still belongs to a product area if one is named.
        area = next((cat for cat, score in ranked if cat != C.PERFORMANCE and score > 0), None)
        lowered = text.lower()
        mentioned = next((s for s in self.services if s in lowered), None)
        hinted = next((svc for word, svc in SERVICE_HINTS.items() if word in lowered), None)
        kb_service, kb_source = self._kb_service(text) if not (mentioned or hinted) else (None, None)
        service = mentioned or CATEGORY_SERVICE.get(category) or hinted or CATEGORY_SERVICE.get(area) or kb_service
        service_source = (
            "named in the ticket" if mentioned else
            "category default" if CATEGORY_SERVICE.get(category) == service else
            "product area" if hinted == service else
            "secondary category" if area and CATEGORY_SERVICE.get(area) == service else
            f"knowledge base ({kb_source})" if kb_service == service and service else "none"
        )  # fmt: skip

        priority, priority_reasons = self._priority(text, category)
        if category == C.OTHER:
            confidence = 0.2
        else:
            margin = (top_score - second_score) / top_score
            confidence = 0.45 + 0.4 * margin + (0.05 if mentioned else 0) + min(0.07, 0.02 * top_score)
        confidence = round(min(0.95, confidence), 3)

        words = ", ".join(hits[category]) if category != C.OTHER else "no category keywords"
        rationale = (
            f"{category.value.title()} keywords: {words}. Priority {priority.value}"
            + (f" ({'; '.join(priority_reasons)})" if priority_reasons else " (no impact phrases)")
            + f". Service: {service or 'unknown'} ({service_source})."
        )
        return Prediction(
            category=category,
            priority=priority,
            service=service,
            confidence=confidence,
            rationale=rationale,
            engine=self.name,
            signals={
                "category_hits": {c.value: w for c, w in hits.items() if w},
                "scores": {c.value: s for c, s in scores.items() if s},
                "priority": priority_reasons,
                "service_source": service_source,
            },
        )


SYSTEM_PROMPT = """You triage customer support tickets for ShopStream, an e-commerce and payments platform. \
Classify the ticket so it reaches the right team with the right urgency. A human analyst reviews every suggestion.

Categories: PAYMENTS (checkout, card payments, refunds, payouts), AUTHENTICATION (login, sessions, password reset, \
MFA), ORDERS (orders, invoices, exports, inventory), NOTIFICATIONS (e-mails, webhooks), DATABASE (data access errors), \
PERFORMANCE (slowness without a clearer product area), OTHER.

Priorities: P1 = a core flow (checkout, login) failing for many customers; P2 = a core flow degraded or a secondary \
flow failing for many; P3 = minor degradation or a single customer; P4 = cosmetic or a question.

Base the answer only on the ticket text. confidence is your probability (0-1) that category, priority and service \
are all correct; use lower values when the text is vague. rationale: one or two sentences quoting the words that \
decided it."""


class LLMClassifier:
    def __init__(self, llm: LLMClient, services: dict[str, str], fallback: Classifier) -> None:
        self.llm = llm
        self.services = services  # name -> description
        self.fallback = fallback
        self.name = llm.name

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": [c.value for c in TicketCategory]},
                "priority": {"type": "string", "enum": [p.value for p in Priority]},
                "service": {"type": "string", "enum": [*self.services, "unknown"]},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
            },
            "required": ["category", "priority", "service", "confidence", "rationale"],
            "additionalProperties": False,
        }

    def classify(self, title: str, description: str) -> Prediction:
        catalogue = "\n".join(f"- {name}: {desc}" for name, desc in self.services.items())
        prompt = f"SERVICES:\n{catalogue}\n\nTICKET TITLE: {title}\nTICKET DESCRIPTION:\n{description}"
        try:
            data = self.llm.complete_json(SYSTEM_PROMPT, prompt, self.schema(), max_tokens=2048)
            prediction = Prediction(
                category=TicketCategory(data["category"]),
                priority=Priority(data["priority"]),
                service=None if data["service"] == "unknown" else data["service"],
                confidence=round(min(1.0, max(0.0, float(data["confidence"]))), 3),
                rationale=str(data["rationale"]).strip(),
                engine=self.name,
            )
        except (LLMError, KeyError, ValueError, TypeError) as exc:
            log.warning("LLM classifier failed, using %s: %s", self.fallback.name, exc)
            prediction = self.fallback.classify(title, description)
            prediction.signals["llm_error"] = str(exc)
            return prediction
        if prediction.service is not None and prediction.service not in self.services:
            prediction.service = None
        return prediction


def get_classifier(db: Session) -> Classifier:
    from app.ai.llm import get_llm

    keywords = KeywordClassifier(db)
    llm = get_llm()
    if llm is None:
        return keywords
    return LLMClassifier(llm, {s.name: s.description for s in db.query(Service).order_by(Service.name)}, keywords)
