"""Known failure modes the rules engine can recognise.

A hypothesis is only *proposed* by its log signature; whether it wins depends on the evidence collected for it
(see engine.py). Templates are filled from the attributes of a real log line (`{provider}`, `{timeout}`, ...), so the
root-cause text never contains a value that is not in the data.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryTemplate:
    """A diagnostic query proposed with a hypothesis. {start}/{end} = the investigation window (UTC, ISO 8601), so
    the query returns the same rows whenever it is run; {lookback} = 30 minutes before the window."""

    title: str
    purpose: str  # what the result confirms or rules out
    sql: str


@dataclass(frozen=True)
class Hypothesis:
    key: str
    signature: str  # substring of the log message that identifies the failure mode
    root_cause: str  # str.format template: {service}, {count} + attributes of a sample log line
    steps: tuple[str, ...]
    queries: tuple[QueryTemplate, ...]  # run against the ShopStream application database (read-only sandbox)
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
        queries=(
            QueryTemplate(
                "Payment outcomes during the incident",
                "Confirms that payments fail as TIMEOUT (gateway) rather than DECLINED (issuer), and sizes the impact.",
                "SELECT status, COUNT(*) AS payments, ROUND(AVG(gateway_ms)) AS avg_gateway_ms\n"
                "FROM payments\n"
                "WHERE created_at BETWEEN '{start}' AND '{end}'\n"
                "GROUP BY status\n"
                "ORDER BY payments DESC;",
            ),
            QueryTemplate(
                "Timeouts per minute",
                "Shows when the timeouts started and whether they are still happening.",
                "SELECT date_trunc('minute', created_at) AS minute,\n"
                "       COUNT(*) FILTER (WHERE status = 'TIMEOUT') AS timeouts,\n"
                "       COUNT(*) AS attempts\n"
                "FROM payments\n"
                "WHERE created_at BETWEEN '{lookback}' AND '{end}'\n"
                "GROUP BY 1\n"
                "ORDER BY 1;",
            ),
            QueryTemplate(
                "Orders affected by timed-out payments",
                "Lists the customers' orders to reconcile and, if needed, contact.",
                "SELECT o.id AS order_id, o.merchant_id, o.total, o.status AS order_status, p.created_at\n"
                "FROM payments AS p\n"
                "JOIN orders AS o ON o.id = p.order_id\n"
                "WHERE p.status = 'TIMEOUT' AND p.created_at BETWEEN '{start}' AND '{end}'\n"
                "ORDER BY p.created_at DESC;",
            ),
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
        queries=(
            QueryTemplate(
                "Sessions holding database connections",
                "Finds the long-running statements that keep the pool exhausted (run during the incident).",
                "SELECT pid, application_name, state, now() - query_start AS runtime, left(query, 120) AS query\n"
                "FROM pg_stat_activity\n"
                "WHERE state <> 'idle'\n"
                "ORDER BY runtime DESC NULLS LAST;",
            ),
            QueryTemplate(
                "Orders per minute around the incident",
                "Checks whether checkouts slowed down while the pool was exhausted (customer impact).",
                "SELECT date_trunc('minute', created_at) AS minute, COUNT(*) AS orders,\n"
                "       COUNT(*) FILTER (WHERE status = 'CONFIRMED') AS confirmed\n"
                "FROM orders\n"
                "WHERE created_at BETWEEN '{lookback}' AND '{end}'\n"
                "GROUP BY 1\n"
                "ORDER BY 1;",
            ),
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
        queries=(
            QueryTemplate(
                "Password reset outcomes",
                "Confirms that resets fail with NotificationDeliveryError: at the e-mail step, not in auth-service.",
                "SELECT status, error, COUNT(*) AS requests\n"
                "FROM password_reset_requests\n"
                "WHERE created_at BETWEEN '{start}' AND '{end}'\n"
                "GROUP BY status, error\n"
                "ORDER BY requests DESC;",
            ),
            QueryTemplate(
                "First and last failed reset",
                "Lines up the onset with the SMTP credential change and shows whether failures are ongoing.",
                "SELECT MIN(created_at) AS first_failure, MAX(created_at) AS last_failure, COUNT(*) AS failures\n"
                "FROM password_reset_requests\n"
                "WHERE status = 'FAILED' AND created_at >= '{lookback}';",
            ),
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
