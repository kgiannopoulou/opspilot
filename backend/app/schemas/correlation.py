from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PatternSignalOut(BaseModel):
    ref: str
    level: str
    message: str
    count: int
    baseline_count: int
    new: bool
    first_seen: datetime
    sample: dict[str, Any]


class MetricSignalOut(BaseModel):
    ref: str
    metric: str
    label: str
    baseline: float
    current: float
    change_pct: float
    anomalous: bool
    display: str


class ServiceSignalsOut(BaseModel):
    service: str
    relation: str
    status: str
    summary: str
    patterns: list[PatternSignalOut]
    metrics: list[MetricSignalOut]


class SuspectedIncident(BaseModel):
    number: str
    title: str
    services: list[str]


class CorrelationOut(BaseModel):
    ticket: str
    anchor: datetime
    window_start: datetime
    window_end: datetime
    headline: str
    services: list[ServiceSignalsOut]
    suspected_incident: SuspectedIncident | None


class LinkTicket(BaseModel):
    ticket_number: str = Field(min_length=3, max_length=20)
