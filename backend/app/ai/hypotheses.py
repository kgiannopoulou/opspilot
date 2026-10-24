"""Known failure modes the rules engine can recognise.

A hypothesis is only *proposed* by its log signature; whether it wins depends on the evidence collected for it
(see engine.py). Templates are filled from the attributes of a real log line (`{provider}`, `{timeout}`, ...), so the
root-cause text never contains a value that is not in the data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hypothesis:
    key: str
    signature: str  # substring of the log message that identifies the failure mode
    root_cause: str  # str.format template: {service}, {count} + attributes of a sample log line
    steps: tuple[str, ...]
    suggested_sql: str | None  # {minutes} = length of the investigation window
    doc_tags: tuple[str, ...]
    corroborating: tuple[str, ...] = ()  # other log messages that strengthen the hypothesis when present
    implicates: tuple[str, ...] = ()  # services whose anomalies support the hypothesis besides the emitter


HYPOTHESES: tuple[Hypothesis, ...] = (
    Hypothesis(
        key="payment_gateway_timeout",
        signature="GatewayTimeoutException",
        root_cause=(
            "Payment gateway requests from {service} are exceeding the configured {timeout} client timeout "
            "(provider={provider}); checkouts fail when the gateway call times out."
        ),
        steps=(
            "Check the {provider} status page and #payments-alerts for a provider-side degradation",
            "Inspect payment-service timeout configuration (PAYMENT_GATEWAY_TIMEOUT_MS, currently {timeout})",
            "Query affected transactions to size the customer impact",
            "Compare gateway latency against the previous 24 hours",
        ),
        suggested_sql=(
            "SELECT id, order_id, status, created_at\n"
            "FROM payments\n"
            "WHERE status = 'TIMEOUT'\n"
            "  AND created_at > NOW() - INTERVAL '{minutes} minutes'\n"
            "ORDER BY created_at DESC;"
        ),
        doc_tags=("GatewayTimeoutException", "gateway", "checkout"),
        corroborating=("Payment failed",),
    ),
    Hypothesis(
        key="db_connection_pool_exhaustion",
        signature="QueuePool limit",
        root_cause=(
            "{service} cannot obtain database connections: its pool is exhausted while postgres-primary is busy "
            "with slow statements, so requests wait the full {timeout} pool timeout."
        ),
        steps=(
            "List the sessions holding connections in pg_stat_activity (query below)",
            "Cancel long-running reporting queries and pause the scheduled job that started before the incident",
            "Check the slow statement's plan for sequential scans and add the missing index",
            "Move reporting workloads to the read replica",
        ),
        suggested_sql=(
            "SELECT pid, application_name, state, now() - query_start AS runtime, left(query, 120) AS query\n"
            "FROM pg_stat_activity\n"
            "WHERE datname = 'shopstream' AND state <> 'idle'\n"
            "ORDER BY runtime DESC\n"
            "LIMIT 20;"
        ),
        doc_tags=("QueuePool", "connection-pool", "dashboard"),
        corroborating=("slow statement",),
        implicates=("postgres-primary",),
    ),
    Hypothesis(
        key="smtp_authentication_failure",
        signature="SMTPAuthenticationError",
        root_cause=(
            "{service} cannot authenticate to the SMTP relay {relay} ({code}: {detail}); e-mail-dependent flows "
            "such as password reset fail with HTTP 500."
        ),
        steps=(
            "Compare the time of the last SMTP credential change with the first SMTPAuthenticationError",
            "Roll back the smtp/relay secret to the previous version and reload notification-service",
            "Verify that password-reset requests return 200 again",
            "Schedule credential rotation with an overlap window",
        ),
        suggested_sql=(
            "SELECT user_id, status, created_at\n"
            "FROM password_reset_requests\n"
            "WHERE status = 'FAILED'\n"
            "  AND created_at > NOW() - INTERVAL '{minutes} minutes'\n"
            "ORDER BY created_at DESC;"
        ),
        doc_tags=("SMTPAuthenticationError", "smtp", "password-reset"),
        corroborating=("POST /auth/password-reset 500",),
    ),
)

# Log messages that mark a change in the system: the usual suspects when something breaks right after.
CHANGE_MARKERS = ("Configuration reloaded", "Scheduled job started", "Deployment finished", "Feature flag changed")

UNKNOWN_STEPS = (
    "Review the first occurrences of '{message}' in {service} logs",
    "Check deployments and configuration changes shortly before the onset",
    "Compare {service} and its dependencies against their baseline metrics",
    "Escalate to the owning team ({team}) if the signal is not understood within 30 minutes",
)
