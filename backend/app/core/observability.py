"""Metrics, request ids and structured logs.

    GET /metrics   Prometheus text format (optionally behind OPSPILOT_METRICS_TOKEN)
    X-Request-ID   accepted from the caller (or generated), echoed in the response and in every log line

Two kinds of metrics:
* events counted where they happen — HTTP requests, LLM calls and tokens, retrieval, investigations, jobs;
* state read at scrape time from the systems of record — queue depth from Redis, open tickets, active incidents and
  MTTR from PostgreSQL — cached for a few seconds so a scrape never hammers the database.

Label values are bounded (route templates, not URLs; enum values; model names), so cardinality stays small.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("opspilot.http")
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
PROBES = {"/health", "/ready", "/metrics"}

REGISTRY = CollectorRegistry(auto_describe=True)
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
AI_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 120)

HTTP_REQUESTS = Counter("opspilot_http_requests_total", "HTTP requests", ["method", "route", "status"],
                        registry=REGISTRY)  # fmt: skip
HTTP_LATENCY = Histogram("opspilot_http_request_duration_seconds", "HTTP request latency", ["method", "route"],
                         buckets=LATENCY_BUCKETS, registry=REGISTRY)  # fmt: skip
LLM_LATENCY = Histogram("opspilot_llm_request_duration_seconds", "LLM call latency", ["model", "outcome"],
                        buckets=AI_BUCKETS, registry=REGISTRY)  # fmt: skip
LLM_TOKENS = Counter("opspilot_llm_tokens_total", "LLM tokens", ["model", "direction"], registry=REGISTRY)
RETRIEVAL_LATENCY = Histogram("opspilot_rag_retrieval_duration_seconds", "Retrieval: embed + pgvector + re-rank",
                              buckets=LATENCY_BUCKETS, registry=REGISTRY)  # fmt: skip
INVESTIGATIONS = Counter("opspilot_investigations_total", "Investigations by outcome and diagnosis",
                         ["outcome", "hypothesis"], registry=REGISTRY)  # fmt: skip
INVESTIGATION_LATENCY = Histogram("opspilot_investigation_duration_seconds", "Investigation pipeline duration",
                                  ["outcome"], buckets=AI_BUCKETS, registry=REGISTRY)  # fmt: skip
WRITEUPS = Counter("opspilot_writeups_total", "Write-ups by writer and grounding verdict", ["writer", "grounding"],
                   registry=REGISTRY)  # fmt: skip
JOBS = Counter("opspilot_jobs_total", "Background jobs by type and outcome", ["type", "outcome"], registry=REGISTRY)
JOB_LATENCY = Histogram("opspilot_job_duration_seconds", "Background job duration", ["type"], buckets=AI_BUCKETS,
                        registry=REGISTRY)  # fmt: skip
TICKETS_CREATED = Counter("opspilot_tickets_created_total", "Tickets created", ["priority"], registry=REGISTRY)


def observe_llm(model: str, outcome: str, seconds: float, usage: Any = None) -> None:
    LLM_LATENCY.labels(model, outcome).observe(seconds)
    for direction in ("input", "output"):
        tokens = getattr(usage, f"{direction}_tokens", None) if usage is not None else None
        if tokens:
            LLM_TOKENS.labels(model, direction).inc(tokens)


# -- state read at scrape time ---------------------------------------------------------------------------------


class StateCollector(Collector):
    """Gauges computed from Redis and PostgreSQL when Prometheus scrapes, cached for `ttl` seconds."""

    def __init__(self, session_factory: Any = None, redis_factory: Any = None, ttl: float = 10.0) -> None:
        self.session_factory, self.redis_factory = session_factory, redis_factory
        self.ttl, self._at = ttl, 0.0
        self._cache: list[GaugeMetricFamily] = []

    def collect(self) -> Iterable[GaugeMetricFamily]:
        if time.monotonic() - self._at > self.ttl:
            self._cache, self._at = list(self._compute()), time.monotonic()
        yield from self._cache

    def _compute(self) -> Iterable[GaugeMetricFamily]:
        from app.ai.llm import llm_status

        up = GaugeMetricFamily("opspilot_dependency_up", "1 if the dependency answered the last scrape",
                               labels=["dependency"])  # fmt: skip
        yield from self._queue(up)
        yield from self._database(up)
        yield up
        status = llm_status()
        circuit = GaugeMetricFamily("opspilot_llm_circuit_open", "1 while the LLM circuit breaker is open")
        circuit.add_metric([], 1.0 if status["circuit"] in ("open", "half_open") else 0.0)
        yield circuit

    def _queue(self, up: GaugeMetricFamily) -> Iterable[GaugeMetricFamily]:
        import redis

        from app.auth.rate_limit import get_redis
        from app.workers.queue import JobQueue

        depth = GaugeMetricFamily("opspilot_queue_messages", "Job queue depth", labels=["state"])
        try:
            for state, n in JobQueue((self.redis_factory or get_redis)()).stats().items():
                depth.add_metric([state], n)
            up.add_metric(["redis"], 1)
        except redis.RedisError:
            up.add_metric(["redis"], 0)
        yield depth

    def _database(self, up: GaugeMetricFamily) -> Iterable[GaugeMetricFamily]:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError

        from app.core.database import SessionLocal

        open_tickets = GaugeMetricFamily("opspilot_tickets_open", "Open tickets", labels=["priority"])
        active = GaugeMetricFamily("opspilot_incidents_active", "Unresolved incidents", labels=["severity"])
        mttr = GaugeMetricFamily("opspilot_incident_mttr_seconds", "Mean time to resolve, incidents resolved in the "
                                 "last 30 days")  # fmt: skip
        jobs = GaugeMetricFamily("opspilot_background_jobs", "Background jobs by status (PostgreSQL)",
                                 labels=["status"])  # fmt: skip
        try:
            with (self.session_factory or SessionLocal)() as db:
                open_sql = "SELECT priority, count(*) FROM tickets WHERE status IN ('OPEN','TRIAGED','INVESTIGATING')"
                for priority, n in db.execute(text(open_sql + " GROUP BY priority")):
                    open_tickets.add_metric([priority], n)
                for severity, n in db.execute(text("SELECT severity, count(*) FROM incidents "
                                                   "WHERE status <> 'RESOLVED' GROUP BY severity")):  # fmt: skip
                    active.add_metric([severity], n)
                value = db.scalar(text("SELECT avg(extract(epoch FROM resolved_at - started_at)) FROM incidents "
                                       "WHERE resolved_at > now() - interval '30 days'"))  # fmt: skip
                mttr.add_metric([], float(value or 0))
                for status, n in db.execute(text("SELECT status, count(*) FROM background_jobs GROUP BY status")):
                    jobs.add_metric([status], n)
            up.add_metric(["postgres"], 1)
        except SQLAlchemyError:
            up.add_metric(["postgres"], 0)
        yield from (open_tickets, active, mttr, jobs)


STATE = StateCollector()


def register_state_collector() -> None:
    REGISTRY.register(STATE)


def metrics_response() -> Response:
    return Response(generate_latest(REGISTRY), media_type="text/plain; version=0.0.4; charset=utf-8")


# -- request ids, HTTP metrics, access log -------------------------------------------------------------------


def route_label(request: Request, api_prefix: str) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return "unmatched"  # 404s: never label by raw URL (unbounded cardinality)
    if request.url.path.startswith(api_prefix) and not path.startswith(api_prefix):
        path = api_prefix + path  # routers are included lazily; their routes carry the path without the prefix
    return path


async def observe_request(request: Request, call_next: Any, api_prefix: str) -> Response:
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
    token = request_id_var.set(rid)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        elapsed = time.perf_counter() - started
        route = route_label(request, api_prefix)
        probe = route in PROBES
        if route != "/metrics":
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)
        # Probes and scrapes arrive every few seconds: keep them out of the access log unless they fail.
        log.log(logging.DEBUG if probe and status < 400 else logging.INFO, "request",
                extra={"method": request.method, "route": route, "status": status,
                       "duration_ms": round(elapsed * 1000, 1)})  # fmt: skip
        request_id_var.reset(token)


# -- structured logging ----------------------------------------------------------------------------------------

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line with the request id, ready for Loki / CloudWatch / Elasticsearch."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable for local development: the message, then the structured fields as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(f"{k}={v}" for k, v in vars(record).items() if k not in _STANDARD)
        line = super().format(record)
        return f"{line} request_id={request_id_var.get()} {extras}".rstrip()


def configure_logging(fmt: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    if fmt == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by the access log above (with request ids)
