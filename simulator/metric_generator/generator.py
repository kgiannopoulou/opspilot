"""Metric samples for every service: noisy baselines, optionally overridden by an active incident."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from simulator.records import MetricSample
from simulator.topology import SERVICES, MetricBaseline

# Returns a replacement value for (service, metric, timestamp, healthy_value), or None to keep the healthy one.
MetricOverride = Callable[[str, str, datetime, float], float | None]


def _daily_load_factor(ts: datetime) -> float:
    """Traffic peaks around 19:00 UTC and is lowest around 04:00 UTC."""
    hours = ts.hour + ts.minute / 60
    return 1 + 0.25 * math.sin((hours - 13) / 24 * 2 * math.pi)


def _sample(rng: random.Random, baseline: MetricBaseline, load: float, metric: str) -> float:
    mean = baseline.mean
    if metric in {"requests_per_min", "cpu_usage", "database_connections"}:
        mean *= load
    value = rng.gauss(mean, baseline.jitter)
    return min(max(value, baseline.low), baseline.high)


def generate_metrics(
    rng: random.Random,
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(minutes=1),
    overrides: list[MetricOverride] | None = None,
) -> Iterator[MetricSample]:
    ts = start
    while ts < end:
        load = _daily_load_factor(ts)
        for service in SERVICES.values():
            for name, baseline in service.metrics.items():
                value = _sample(rng, baseline, load, name)
                for override in overrides or ():
                    replaced = override(service.name, name, ts, value)
                    if replaced is not None:
                        value = replaced
                yield MetricSample(ts, service.name, name, round(value, 4), baseline.unit)
        ts += step
