from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class LogRecord:
    timestamp: datetime
    service: str
    level: str  # DEBUG | INFO | WARN | ERROR
    message: str
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None

    def format(self) -> str:
        """Render the line the way a log shipper would print it."""
        attrs = " ".join(f"{k}={v}" for k, v in self.attributes.items())
        ts = self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{ts} {self.level:<5} {self.service}  {self.message}" + (f" {attrs}" if attrs else "")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass(frozen=True)
class MetricSample:
    timestamp: datetime
    service: str
    name: str
    value: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data
