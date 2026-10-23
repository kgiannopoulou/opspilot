"""The fictional ShopStream platform: services, their dependencies and healthy metric baselines."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricBaseline:
    """Healthy behaviour of one metric: values are drawn from N(mean, jitter) and clamped to [low, high]."""

    mean: float
    jitter: float
    unit: str
    low: float = 0.0
    high: float = float("inf")


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    display_name: str
    tier: int  # 1 = customer-facing critical path
    owner_team: str
    description: str
    dependencies: tuple[str, ...] = ()
    metrics: dict[str, MetricBaseline] = field(default_factory=dict)


def _app_metrics(latency_ms: float, rpm: float) -> dict[str, MetricBaseline]:
    return {
        "api_latency_p95_ms": MetricBaseline(latency_ms, latency_ms * 0.08, "ms", low=1),
        "error_rate": MetricBaseline(0.004, 0.002, "ratio", high=1),
        "requests_per_min": MetricBaseline(rpm, rpm * 0.1, "rpm"),
        "cpu_usage": MetricBaseline(0.38, 0.05, "ratio", high=1),
        "memory_usage": MetricBaseline(0.62, 0.03, "ratio", high=1),
    }


SERVICES: dict[str, ServiceSpec] = {
    spec.name: spec
    for spec in (
        ServiceSpec(
            name="auth-service",
            display_name="Authentication Service",
            tier=1,
            owner_team="Identity",
            description="Login, session tokens and password reset.",
            dependencies=("postgres-primary", "notification-service"),
            metrics=_app_metrics(latency_ms=85, rpm=900),
        ),
        ServiceSpec(
            name="payment-service",
            display_name="Payment Service",
            tier=1,
            owner_team="Payments",
            description="Card payments through the external payment gateway (provider=stripe).",
            dependencies=("postgres-primary",),
            metrics=_app_metrics(latency_ms=1040, rpm=220),
        ),
        ServiceSpec(
            name="order-service",
            display_name="Order Service",
            tier=1,
            owner_team="Checkout",
            description="Cart, checkout orchestration, order history and the merchant dashboard API.",
            dependencies=("payment-service", "auth-service", "postgres-primary", "notification-service"),
            metrics=_app_metrics(latency_ms=240, rpm=480),
        ),
        ServiceSpec(
            name="notification-service",
            display_name="Notification Service",
            tier=2,
            owner_team="Messaging",
            description="Transactional e-mail (SMTP relay) and webhooks.",
            dependencies=(),
            metrics=_app_metrics(latency_ms=310, rpm=160),
        ),
        ServiceSpec(
            name="postgres-primary",
            display_name="PostgreSQL Primary",
            tier=1,
            owner_team="Platform",
            description="Shared relational database (max_connections=100, pool size 20 + 10 overflow per app).",
            dependencies=(),
            metrics={
                "database_connections": MetricBaseline(42, 4, "count", high=100),
                "query_latency_p95_ms": MetricBaseline(12, 1.5, "ms", low=1),
                "cpu_usage": MetricBaseline(0.31, 0.04, "ratio", high=1),
                "memory_usage": MetricBaseline(0.71, 0.02, "ratio", high=1),
            },
        ),
    )
}


def dependents_of(service: str) -> list[str]:
    """Services that call `service` directly (who is hurt when it fails)."""
    return [s.name for s in SERVICES.values() if service in s.dependencies]
