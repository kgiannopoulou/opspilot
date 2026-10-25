"""Retrieval-augmented generation: chunking, embeddings, pgvector search and the LLM write-up stage."""

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai.chunking import chunk_markdown
from app.ai.embeddings import HashingEmbedder, tokenize
from app.ai.llm import LLMError
from app.ai.writer import DRAFT_SCHEMA, LLMWriter, render_prompt
from app.models import DocumentChunk, Incident, User
from app.scripts.seed import seed
from app.services.investigations import InvestigationService
from app.services.knowledge import load_knowledge_base
from tests.conftest import as_user

DOC = """# Title

Intro paragraph.

## Triage

1. Check the status page.

### Deep dive

Look at the logs.

```sql
## not a heading inside code
SELECT 1;
```

## Mitigation

Raise the timeout.
"""


@pytest.fixture
def demo(db):
    seed(db, now=datetime.now(UTC))
    users = {u.email.split("@")[0]: u for u in db.query(User)}
    incidents = {i.title: i for i in db.scalars(select(Incident)).unique()}
    return users, incidents


def test_chunks_follow_numbered_headings():
    chunks = chunk_markdown("Runbook", DOC)
    assert [(c.section, c.heading) for c in chunks] == [
        ("0", "Overview"),
        ("1", "Triage"),
        ("1.1", "Triage › Deep dive"),
        ("2", "Mitigation"),
    ]
    assert "## not a heading inside code" in chunks[2].content  # fenced code is never split
    assert chunks[1].embed_text.startswith("Runbook › Triage")


def test_long_sections_are_split_on_paragraphs():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(5))
    chunks = chunk_markdown("Doc", f"## Big\n\n{body}")
    assert len(chunks) > 1 and {c.section for c in chunks} == {"1"}
    assert all(c.content.startswith("Paragraph") for c in chunks)


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder()
    a, b = embedder.embed(["Payment gateway timeout", "Payment gateway timeout"])
    assert a == b and len(a) == 384
    assert abs(sum(v * v for v in a) - 1) < 1e-9
    # camelCase signatures are split, and customer words map to engineering words.
    assert {"gatewaytimeoutexception", "gateway", "timeout"} <= set(tokenize("GatewayTimeoutException"))
    assert "timeout" in tokenize("checkout freezes")


def test_semantic_search_finds_runbooks_from_customer_language(client, demo):
    users, _ = demo
    headers = as_user(users["viewer"])
    cases = {
        "checkout freezes after clicking pay": "payment-gateway-timeout",
        "password reset returns HTTP 500": "password-reset-errors",
        "database connections exhausted": "db-connection-pool-exhaustion",
        "CPU is very high after a config change": "high-cpu-runbook",
    }
    for query, slug in cases.items():
        body = client.get("/api/v1/knowledge/search", params={"q": query}, headers=headers).json()
        assert body["embedding_model"] == "hashing-v1"
        assert body["hits"][0]["slug"] == slug, query
        assert body["hits"][0]["section"] and body["hits"][0]["content"]
    assert client.get("/api/v1/knowledge/search", params={"q": "x"}, headers=headers).status_code == 422


def test_switching_embedding_model_reindexes_everything(db, demo):
    class OtherModel(HashingEmbedder):
        name = "other-model"

    stats = load_knowledge_base(db, embedder=OtherModel())
    assert stats["reindexed"] == 12 and stats["unchanged"] == 12 and stats["chunks"] > 40
    assert set(db.scalars(select(DocumentChunk.embedding_model))) == {"other-model"}


class FakeLLM:
    name = "fake-model"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def complete_json(self, system, prompt, schema, max_tokens=4096):
        self.calls.append((system, prompt, schema))
        if self.error:
            raise self.error
        return self.answer(prompt) if callable(self.answer) else self.answer


def first_ref(prompt: str, prefix: str) -> str:
    return re.search(rf'"ref": "({prefix}-[^"]+)"', prompt).group(1)


def test_llm_writer_receives_only_collected_evidence(db, demo):
    _, incidents = demo
    incident = incidents["HTTP 500 on password reset"]
    llm = FakeLLM(
        lambda prompt: {
            "root_cause": "SMTP authentication fails after the credential change.",
            "narrative": f"notification-service cannot log in to the relay [{first_ref(prompt, 'LOG')}].",
            "recommended_steps": ["Roll back the smtp/relay secret", " "],
        }
    )
    run = InvestigationService(db, writer=LLMWriter(llm)).run(incident, None)

    assert run.engine == "rules-v1+rag/claude/fake-model"
    assert run.root_cause.startswith("SMTP authentication fails")
    assert run.recommended_steps == ["Roll back the smtp/relay secret"]  # blank steps dropped
    system, prompt, schema = llm.calls[0]
    assert schema == DRAFT_SCHEMA and "Use only facts present" in system
    assert "SMTPAuthenticationError" in prompt and "PASSAGES" in prompt
    assert prompt == render_prompt(incident, _result_of(db, incident))


def test_llm_failure_falls_back_to_extractive_write_up(db, demo):
    _, incidents = demo
    incident = incidents["HTTP 500 on password reset"]
    run = InvestigationService(db, writer=LLMWriter(FakeLLM(error=LLMError("model provider unreachable")))).run(
        incident, None
    )
    assert run.status.value == "COMPLETED"
    assert run.engine == "rules-v1+rag/extractive-v1"
    assert run.grounding["passed"] and run.grounding["llm_error"] == "model provider unreachable"


def _result_of(db, incident):
    from app.ai.engine import InvestigationEngine

    return InvestigationEngine(db).investigate(incident)
