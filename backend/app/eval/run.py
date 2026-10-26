"""Offline evaluation of OpsPilot's AI: triage classification, knowledge-base retrieval and investigations.

    python -m app.eval.run                       # all suites → docs/evaluation/{results.json,README.md}
    python -m app.eval.run --suite retrieval     # one suite, printed only
    OPSPILOT_LLM_PROVIDER=anthropic python -m app.eval.run   # same benchmark with the LLM writer and classifier

It builds its own database (OPSPILOT_EVAL_DATABASE_URL, default `opspilot_eval`) from the migrations, loads the
service catalogue, 60 days of incident history and the knowledge base, then:

* triage      — 50 hand-labelled tickets → category / priority / service accuracy
* retrieval   — 40 queries with the documents an expert would open → Recall@k, MRR
* investigate — 41 simulated incidents whose fault is known because it was injected → root-cause accuracy,
                runbook recall, citation validity, calibration and latency

The report is generated from the results, never typed by hand, and lists every miss.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.ai.classifier import get_classifier
from app.ai.embeddings import get_embedder
from app.ai.engine import ENGINE_VERSION, InvestigationEngine
from app.ai.retrieval import KnowledgeRetriever
from app.ai.writer import Writer, get_writer
from app.eval.cases import InvestigationCase, build_cases
from app.models import ApplicationLog, MetricSample, Service
from app.models.enums import EvidenceKind, Severity
from app.repositories.observability import bulk_insert_app_data, bulk_insert_logs, bulk_insert_metrics
from app.services.incidents import IncidentService
from app.services.knowledge import load_knowledge_base
from simulator.app_data import derive_app_data
from simulator.engine import ScheduledIncident, simulate

DATASETS = Path(__file__).parent / "datasets"
REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "docs" / "evaluation"
DEFAULT_DB = "postgresql+psycopg://opspilot:opspilot@127.0.0.1:5438/opspilot_eval"
UNKNOWN_MAX_CONFIDENCE = 0.35


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((DATASETS / f"{name}.json").read_text(encoding="utf-8"))["cases"]


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _latency(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))] if ordered else 0.0
    return {"median_ms": round(statistics.median(ordered), 1) if ordered else 0.0, "p95_ms": round(p95, 1)}


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


# -- environment -------------------------------------------------------------------------------------------------


def prepare(db: Session, now: datetime) -> None:
    """Empty database → service catalogue, incident history (for "similar incident" evidence), knowledge base."""
    from app.scripts.seed import reset, seed_catalog, seed_history

    reset(db)
    users, services = seed_catalog(db)
    seed_history(db, now, users, services)
    load_knowledge_base(db)


# -- suites --------------------------------------------------------------------------------------------------------


def run_triage(db: Session, cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cases = cases or _load("triage")
    classifier = get_classifier(db)
    rows, times = [], []
    for case in cases:
        started = time.perf_counter()
        p = classifier.classify(case["title"], case["description"])
        times.append(_ms(started))
        got = {"category": p.category.value, "priority": p.priority.value, "service": p.service}
        rows.append({"id": case["id"], "difficulty": case["difficulty"], "title": case["title"], "got": got,
                     "expected": {k: case[k] for k in ("category", "priority", "service")},
                     "confidence": p.confidence})  # fmt: skip

    def accuracy(field: str, subset: list[dict[str, Any]]) -> float | None:
        return _rate(sum(r["got"][field] == r["expected"][field] for r in subset), len(subset))

    by_difficulty = defaultdict(list)
    for r in rows:
        by_difficulty[r["difficulty"]].append(r)
    return {
        "engine": classifier.name,
        "cases": len(rows),
        "category_accuracy": accuracy("category", rows),
        "priority_accuracy": accuracy("priority", rows),
        "service_accuracy": accuracy("service", rows),
        "all_three_correct": _rate(sum(r["got"] == r["expected"] for r in rows), len(rows)),
        "by_difficulty": {
            d: {
                "cases": len(rs),
                "category": accuracy("category", rs),
                "priority": accuracy("priority", rs),
                "service": accuracy("service", rs),
            }
            for d, rs in sorted(by_difficulty.items())
        },  # fmt: skip
        "latency": _latency(times),
        "misses": [r for r in rows if r["got"] != r["expected"]],
    }


def run_retrieval(db: Session, cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cases = cases or _load("retrieval")
    retriever = KnowledgeRetriever(db)
    rows, times = [], []
    for case in cases:
        started = time.perf_counter()
        passages = retriever.search(case["query"], k=10)
        times.append(_ms(started))
        docs: list[str] = []
        for p in passages:  # distinct documents in rank order
            if p.slug not in docs:
                docs.append(p.slug)
        relevant = set(case["relevant"])
        rank = next((i + 1 for i, d in enumerate(docs) if d in relevant), None)
        rows.append({"id": case["id"], "query": case["query"], "relevant": case["relevant"], "top5": docs[:5],
                     "rank": rank})  # fmt: skip
    n = len(rows)

    def recall(k: int) -> float | None:
        return _rate(sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k), n)

    return {
        "embedding_model": retriever.embedder.name,
        "cases": n,
        "recall_at_1": recall(1),
        "recall_at_3": recall(3),
        "recall_at_5": recall(5),
        "mrr": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3) if n else None,
        "latency": _latency(times),
        "misses": [r for r in rows if r["rank"] is None or r["rank"] > 3],
    }


def _clear_telemetry(db: Session) -> None:
    from app.models import shop_orders, shop_password_resets, shop_payments

    tables = [ApplicationLog.__table__, MetricSample.__table__, shop_orders, shop_payments, shop_password_resets]
    names = ", ".join(f'"{t.schema}"."{t.name}"' if t.schema else f'"{t.name}"' for t in tables)
    db.execute(text(f"TRUNCATE {names}"))
    db.commit()


def run_case(db: Session, case: InvestigationCase, writer: Writer, now: datetime) -> dict[str, Any]:
    _clear_telemetry(db)
    onset = now - timedelta(minutes=case.minutes_running)
    earliest = min([onset, *(onset + timedelta(minutes=f.offset_minutes) for f in case.faults)])
    scheduled = [
        ScheduledIncident(f.scenario, start, now - start)
        for f in case.faults
        for start in [onset + timedelta(minutes=f.offset_minutes)]
    ]
    sim = simulate(earliest - timedelta(hours=2, minutes=15), now, scheduled, seed=case.seed)
    service_ids = {s.name: s.id for s in db.query(Service)}
    bulk_insert_logs(db, sim.logs, service_ids)
    bulk_insert_metrics(db, sim.metrics, service_ids)
    bulk_insert_app_data(db, derive_app_data(sim.logs))
    db.commit()

    incident = IncidentService(db).declare(
        title=case.title, severity=Severity.SEV2, primary_service=case.declared_on[0],
        affected_services=case.declared_on, started_at=onset, actor=None,
    )  # fmt: skip
    started = time.perf_counter()
    result = InvestigationEngine(db).investigate(incident)
    draft = writer.write(incident, result)
    latency = _ms(started)

    documents = [e.ref for e in result.evidence if e.kind == EvidenceKind.DOCUMENT]
    slugs = [db.scalar(text("SELECT slug FROM knowledge_documents WHERE id = :i"), {"i": e.document_id})
             for e in result.evidence if e.kind == EvidenceKind.DOCUMENT]  # fmt: skip
    report = draft.grounding
    db.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident.id})
    db.commit()
    ranked = [key for key, _ in result.alternatives] or [result.hypothesis_key]
    return {
        "id": case.id,
        "group": case.group,
        "faults": [asdict(f) for f in case.faults],
        "declared_on": case.declared_on,
        "minutes_running": case.minutes_running,
        "expected": case.expected_hypothesis,
        "got": result.hypothesis_key,
        "correct": result.hypothesis_key == case.expected_hypothesis,
        "in_top3": case.expected_hypothesis in ranked[:3]
        if case.expected_hypothesis != "unknown"
        else result.hypothesis_key == "unknown",
        "confidence": result.confidence,
        "alternatives": result.alternatives,
        "expected_documents": case.expected_documents,
        "documents": documents,
        "document_hit": bool(set(case.expected_documents) & set(slugs)) if case.expected_documents else None,
        "writer": draft.writer,
        "grounding_passed": bool(report.get("passed")),
        "citations": len(report.get("cited", [])),
        "unknown_refs": report.get("unknown_refs", []),
        "unsupported_numbers": report.get("unsupported_numbers", []),
        "rejected_llm_draft": "rejected_llm_draft" in report,
        "latency_ms": round(latency, 1),
    }


def run_investigations(db: Session, now: datetime, cases: list[InvestigationCase] | None = None) -> dict[str, Any]:
    cases = cases or build_cases()
    writer = get_writer()
    rows = [run_case(db, case, writer, now) for case in cases]
    faults = [r for r in rows if r["expected"] != "unknown"]
    healthy = [r for r in rows if r["expected"] == "unknown"]
    cited = sum(r["citations"] for r in rows)
    invalid = sum(len(r["unknown_refs"]) for r in rows)
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    right = [r["confidence"] for r in faults if r["correct"]]
    wrong = [r["confidence"] for r in faults if not r["correct"]]
    return {
        "engine": ENGINE_VERSION,
        "writer": rows[0]["writer"] if rows else None,
        "cases": len(rows),
        "root_cause_accuracy": _rate(sum(r["correct"] for r in rows), len(rows)),
        "root_cause_accuracy_faults": _rate(sum(r["correct"] for r in faults), len(faults)),
        "root_cause_in_top3": _rate(sum(r["in_top3"] for r in rows), len(rows)),
        "no_fault_low_confidence": _rate(
            sum(r["got"] == "unknown" and r["confidence"] <= UNKNOWN_MAX_CONFIDENCE for r in healthy), len(healthy)
        ),
        "runbook_recall_at_3": _rate(sum(bool(r["document_hit"]) for r in faults), len(faults)),
        "citations": cited,
        "citation_validity": _rate(cited - invalid, cited),
        "unsupported_numbers": sum(len(r["unsupported_numbers"]) for r in rows),
        "grounding_pass_rate": _rate(sum(r["grounding_passed"] for r in rows), len(rows)),
        "llm_drafts_rejected": sum(r["rejected_llm_draft"] for r in rows),
        "mean_confidence_when_correct": round(statistics.mean(right), 3) if right else None,
        "mean_confidence_when_wrong": round(statistics.mean(wrong), 3) if wrong else None,
        "by_group": {
            g: {"cases": len(rs), "accuracy": _rate(sum(r["correct"] for r in rs), len(rs))} for g, rs in groups.items()
        },  # fmt: skip
        "latency": _latency([r["latency_ms"] for r in rows]),
        "misses": [r for r in rows if not r["correct"]],
        "results": rows,
    }


# -- report --------------------------------------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def render_markdown(results: dict[str, Any]) -> str:
    meta, out = results["meta"], []
    out += ["# OpsPilot AI evaluation", "",
            f"Generated by `python -m app.eval.run` on {meta['generated_at'][:10]} at commit `{meta['commit']}` — "
            "every number below comes from [`results.json`](results.json); nothing is typed by hand.", "",
            f"Embeddings `{meta['embedding_model']}` · classifier `{meta['classifier']}` · engine "
            f"`{meta['engine']}` · writer `{meta['writer']}`.", ""]  # fmt: skip
    out += ["## Summary", "", "| Metric | Result | Set |", "|---|---:|---|"]
    t, r, i = results.get("triage"), results.get("retrieval"), results.get("investigate")
    if t:
        out += [f"| Ticket category accuracy | {_pct(t['category_accuracy'])} | {t['cases']} hand-labelled tickets |",
                f"| Ticket priority accuracy | {_pct(t['priority_accuracy'])} | 〃 |",
                f"| Affected-service accuracy | {_pct(t['service_accuracy'])} | 〃 |"]  # fmt: skip
    if r:
        out += [f"| Relevant document Recall@1 / @3 / @5 | {_pct(r['recall_at_1'])} / {_pct(r['recall_at_3'])} / "
                f"{_pct(r['recall_at_5'])} | {r['cases']} queries |",
                f"| Mean reciprocal rank | {r['mrr']:.2f} | 〃 |"]  # fmt: skip
    if i:
        out += [f"| Correct root cause (top-1) | {_pct(i['root_cause_accuracy'])} | {i['cases']} simulated incidents |",
                f"| Runbook retrieved for the injected fault (top-3) | {_pct(i['runbook_recall_at_3'])} "
                "| fault cases |",
                "| \"Nothing found\" with ≤ 35 % confidence when nothing is wrong | "
                f"{_pct(i['no_fault_low_confidence'])} "
                "| no-fault cases |",
                f"| Evidence citation validity | {_pct(i['citation_validity'])} | {i['citations']} citations |",
                f"| Write-ups passing the grounding check | {_pct(i['grounding_pass_rate'])} | all cases |",
                f"| Median / p95 investigation latency | {i['latency']['median_ms']:.0f} ms / "
                f"{i['latency']['p95_ms']:.0f} ms | engine + writer, in-process |"]  # fmt: skip
    out.append("")
    if t:
        out += ["## Triage classification", "",
                "| Difficulty | Tickets | Category | Priority | Service |", "|---|---:|---:|---:|---:|"]  # fmt: skip
        out += [f"| {d} | {v['cases']} | {_pct(v['category'])} | {_pct(v['priority'])} | {_pct(v['service'])} |"
                for d, v in t["by_difficulty"].items()]  # fmt: skip
        out += ["", f"All three fields right: {_pct(t['all_three_correct'])}. Median latency "
                f"{t['latency']['median_ms']:.1f} ms.", "", "<details><summary>Misses</summary>", "",
                "| Ticket | Expected | Got |", "|---|---|---|"]  # fmt: skip
        for m in t["misses"]:
            e, g = m["expected"], m["got"]
            diff = {k: f"**{g[k]}**" if g[k] != e[k] else str(g[k]) for k in e}
            out.append(f"| {m['id']} {m['title']} | {e['category']} · {e['priority']} · {e['service']} | "
                       f"{diff['category']} · {diff['priority']} · {diff['service']} |")  # fmt: skip
        out += ["", "</details>", ""]
    if r:
        out += ["## Knowledge-base retrieval", "",
                f"Recall@k counts a query as answered when any document an expert would open is in the top k distinct "
                f"documents. Median latency {r['latency']['median_ms']:.1f} ms (embedding + pgvector + re-rank).", "",
                "<details><summary>Queries not answered in the top 3</summary>", "",
                "| Query | Relevant | Top 5 | Rank |", "|---|---|---|---:|"]  # fmt: skip
        for m in r["misses"]:
            out.append(f"| {m['query']} | {', '.join(m['relevant'])} | {', '.join(m['top5'])} | {m['rank'] or '—'} |")
        out += ["", "</details>", ""]
    if i:
        out += ["## Investigations", "",
                "Each case runs the simulator with a fault injected (or none), declares the incident the way an "
                "on-call engineer would, and runs the evidence engine and writer against PostgreSQL.", "",
                "| Group | Cases | Correct root cause |", "|---|---:|---:|"]  # fmt: skip
        out += [f"| {g} | {v['cases']} | {_pct(v['accuracy'])} |" for g, v in i["by_group"].items()]
        out += ["", f"Mean confidence when right {i['mean_confidence_when_correct']}, when wrong "
                f"{i['mean_confidence_when_wrong']}. Expected cause among the ranked candidates: "
                f"{_pct(i['root_cause_in_top3'])} — the hypothesis library has three failure modes plus *unknown*, so "
                "top-3 is nearly always satisfied and top-1 is the number that matters.", "",
                f"Citation validity is the share of `[REF]` citations that resolve to evidence of the same run; "
                f"{i['unsupported_numbers']} numbers were not found in the evidence they cite. The extractive writer "
                "passes the grounding check by construction — the meaningful figure is the LLM writer's rejection rate "
                f"({i['llm_drafts_rejected']} drafts rejected in this run; run with "
                "`OPSPILOT_LLM_PROVIDER=anthropic`).",
                "", "<details><summary>Misses</summary>", "",
                "| Case | Group | Faults (declared on) | Expected | Got (confidence) |",
                "|---|---|---|---|---|"]  # fmt: skip
        for m in i["misses"]:
            faults = ", ".join(f["scenario"] for f in m["faults"]) or "none"
            out.append(f"| {m['id']} | {m['group']} | {faults} ({', '.join(m['declared_on'])}) | {m['expected']} | "
                       f"{m['got']} ({m['confidence']:.2f}) |")  # fmt: skip
        if not i["misses"]:
            out.append("| — | | | | |")
        out += ["", "</details>", ""]
    if results.get("history"):
        out += ["## History", "", "Every full run is appended to [`history.json`](history.json), so a change is judged "
                "against the runs before it.", "",
                "| Run (UTC) | Commit | Note | Category | Priority | Recall@1 | Root cause | Runbook@3 |",
                "|---|---|---|---:|---:|---:|---:|---:|"]  # fmt: skip
        for h in results["history"]:
            ht, hr, hi = h.get("triage", {}), h.get("retrieval", {}), h.get("investigate", {})
            out.append(f"| {h['generated_at'][:16].replace('T', ' ')} | `{h['commit']}` | {h['note'] or '—'} | "
                       f"{_pct(ht.get('category_accuracy'))} | {_pct(ht.get('priority_accuracy'))} | "
                       f"{_pct(hr.get('recall_at_1'))} | {_pct(hi.get('root_cause_accuracy'))} | "
                       f"{_pct(hi.get('runbook_recall_at_3'))} |")  # fmt: skip
        out.append("")
    out += ["## Reproduce", "", "```bash", "docker compose up -d postgres redis",
            "cd backend && python -m app.eval.run      # ~2 minutes; writes docs/evaluation/", "```", "",
            "Datasets: [`triage.json`](../../backend/app/eval/datasets/triage.json), "
            "[`retrieval.json`](../../backend/app/eval/datasets/retrieval.json), investigation cases in "
            "[`cases.py`](../../backend/app/eval/cases.py). The regression gate in CI "
            "([`test_evaluation.py`](../../backend/tests/test_evaluation.py)) fails if a change drops below these "
            "results.", ""]  # fmt: skip
    return "\n".join(out)


# -- entry point -------------------------------------------------------------------------------------------------


def _engine_for(url: str):  # noqa: ANN202
    from alembic import command
    from alembic.config import Config

    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}):
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    return create_engine(url)


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["triage", "retrieval", "investigate"], action="append")
    parser.add_argument("--database-url", default=os.environ.get("OPSPILOT_EVAL_DATABASE_URL", DEFAULT_DB))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--note", default="", help="what changed since the last run (kept in history.json)")
    args = parser.parse_args()
    suites = args.suite or ["triage", "retrieval", "investigate"]

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    factory = sessionmaker(bind=_engine_for(args.database_url), autoflush=False, expire_on_commit=False)
    with factory() as db:
        prepare(db, now)
        results: dict[str, Any] = {}
        for suite in suites:
            started = time.perf_counter()
            if suite == "triage":
                results["triage"] = run_triage(db)
            elif suite == "retrieval":
                results["retrieval"] = run_retrieval(db)
            else:
                results["investigate"] = run_investigations(db, now)
            print(f"{suite}: done in {_ms(started) / 1000:.1f} s")
        commit = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--exclude", "*"], capture_output=True, text=True, cwd=REPO
        )
        results["meta"] = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit": commit.stdout.strip() or "unknown",
            "embedding_model": get_embedder().name,
            "classifier": get_classifier(db).name,
            "engine": ENGINE_VERSION,
            "writer": get_writer().name,
        }
    summary = {k: {m: v for m, v in s.items() if not isinstance(v, list | dict)} for k, s in results.items()}
    print(json.dumps(summary, indent=2))
    if args.suite is None:  # a full run updates the published report
        args.out.mkdir(parents=True, exist_ok=True)
        history_file = args.out / "history.json"
        history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else []
        history.append({"commit": results["meta"]["commit"], "generated_at": results["meta"]["generated_at"],
                        "note": args.note, **{k: v for k, v in summary.items() if k != "meta"}})  # fmt: skip
        results["history"] = history
        history_file.write_text(json.dumps(history, indent=2, default=str) + "\n", encoding="utf-8")
        (args.out / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
        (args.out / "README.md").write_text(render_markdown(results), encoding="utf-8")
        print(f"wrote {args.out / 'README.md'}")


if __name__ == "__main__":
    main()
