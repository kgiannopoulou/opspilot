"""Combines healthy traffic with scheduled incidents into one deterministic simulation run."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from simulator.incidents.scenarios import SCENARIOS, Scenario, metric_override
from simulator.log_generator.generator import generate_background_logs
from simulator.metric_generator.generator import generate_metrics
from simulator.records import LogRecord, MetricSample


@dataclass(frozen=True)
class ScheduledIncident:
    scenario_key: str
    start: datetime
    duration: timedelta

    @property
    def end(self) -> datetime:
        return self.start + self.duration

    @property
    def scenario(self) -> Scenario:
        return SCENARIOS[self.scenario_key]


@dataclass
class SimulationResult:
    start: datetime
    end: datetime
    logs: list[LogRecord] = field(default_factory=list)
    metrics: list[MetricSample] = field(default_factory=list)
    incidents: list[ScheduledIncident] = field(default_factory=list)


def simulate(
    start: datetime,
    end: datetime,
    incidents: list[ScheduledIncident] | None = None,
    seed: int = 42,
    metric_step: timedelta = timedelta(minutes=1),
) -> SimulationResult:
    """Same seed + same inputs → byte-identical output, so tests and demos are reproducible."""
    incidents = incidents or []
    for incident in incidents:
        if incident.scenario_key not in SCENARIOS:
            raise ValueError(f"unknown scenario {incident.scenario_key!r}; choose from {sorted(SCENARIOS)}")

    rng = random.Random(seed)
    logs = list(generate_background_logs(rng, start, end))
    for incident in incidents:
        logs.extend(incident.scenario.logs(rng, incident.start, min(incident.end, end)))
    logs = [log for log in logs if start <= log.timestamp < end]
    logs.sort(key=lambda log: log.timestamp)

    overrides = [metric_override(i.scenario, i.start, i.end) for i in incidents]
    metrics = list(generate_metrics(rng, start, end, metric_step, overrides))
    return SimulationResult(start=start, end=end, logs=logs, metrics=metrics, incidents=incidents)
