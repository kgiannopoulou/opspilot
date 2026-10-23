from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models import ApplicationLog, MetricSample
from simulator.records import LogRecord
from simulator.records import MetricSample as SimMetric


def bulk_insert_logs(db: Session, records: Iterable[LogRecord], service_ids: dict[str, int]) -> int:
    rows = [
        {
            "ts": r.timestamp,
            "service_id": service_ids[r.service],
            "level": r.level,
            "message": r.message,
            "attributes": r.attributes,
            "trace_id": r.trace_id,
        }
        for r in records
    ]
    if rows:
        # Core insert (not ORM bulk): one multi-row INSERT per batch instead of per-row statements.
        db.execute(ApplicationLog.__table__.insert(), rows)
    return len(rows)


def bulk_insert_metrics(db: Session, samples: Iterable[SimMetric], service_ids: dict[str, int]) -> int:
    rows = [
        {"ts": m.timestamp, "service_id": service_ids[m.service], "name": m.name, "value": m.value, "unit": m.unit}
        for m in samples
    ]
    if rows:
        db.execute(MetricSample.__table__.insert(), rows)
    return len(rows)
