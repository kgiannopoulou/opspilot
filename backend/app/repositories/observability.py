from collections.abc import Iterable
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import ApplicationLog, MetricSample, shop_orders, shop_password_resets, shop_payments
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
        db.execute(insert(ApplicationLog), rows)
    return len(rows)


def bulk_insert_metrics(db: Session, samples: Iterable[SimMetric], service_ids: dict[str, int]) -> int:
    rows = [
        {"ts": m.timestamp, "service_id": service_ids[m.service], "name": m.name, "value": m.value, "unit": m.unit}
        for m in samples
    ]
    if rows:
        db.execute(insert(MetricSample), rows)
    return len(rows)


def bulk_insert_app_data(db: Session, data: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Load the ShopStream application tables (see simulator.app_data)."""
    tables = {"orders": shop_orders, "payments": shop_payments, "password_reset_requests": shop_password_resets}
    for name, rows in data.items():
        if rows:
            db.execute(tables[name].insert(), rows)
    return {name: len(rows) for name, rows in data.items()}
