"""Regression gate for the AI: the offline benchmark (app/eval) must not get worse than the published results.

Floors are the results in docs/evaluation/README.md. Everything is deterministic (hashing embeddings, seeded
simulator, rules engine), so a drop is a real regression — raise a floor when a change improves the benchmark.
"""

from datetime import UTC, datetime

import pytest

from app.eval import run
from app.eval.cases import build_cases

FLOORS = {
    "triage": {"category_accuracy": 0.82, "priority_accuracy": 0.68, "service_accuracy": 0.80},
    "retrieval": {"recall_at_1": 0.925, "recall_at_3": 1.0, "mrr": 0.96},
    "investigate": {
        "root_cause_accuracy": 0.95,
        "runbook_recall_at_3": 0.94,
        "no_fault_low_confidence": 1.0,
        "citation_validity": 1.0,
        "grounding_pass_rate": 1.0,
    },  # fmt: skip
}


@pytest.fixture
def benchmark_env(db):
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    run.prepare(db, now)
    return db, now


def _assert_floors(suite: str, results: dict) -> None:
    below = {m: results[m] for m, floor in FLOORS[suite].items() if results[m] is None or results[m] < floor}
    assert not below, f"{suite} regressed below the published benchmark: {below}; misses: {results['misses']}"


def test_triage_benchmark(benchmark_env):
    db, _ = benchmark_env
    results = run.run_triage(db)
    assert results["cases"] == 50
    _assert_floors("triage", results)


def test_retrieval_benchmark(benchmark_env):
    db, _ = benchmark_env
    results = run.run_retrieval(db)
    assert results["cases"] == 40
    _assert_floors("retrieval", results)


def test_investigation_benchmark(benchmark_env):
    db, now = benchmark_env
    results = run.run_investigations(db, now)
    assert results["cases"] == len(build_cases()) == 41
    _assert_floors("investigate", results)
