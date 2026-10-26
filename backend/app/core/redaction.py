"""Log redaction: credentials and personal data never reach the log pipeline.

Logs are shipped to systems with wider access than the database (Loki, CloudWatch, a vendor's support portal), so a
secret that is logged once is effectively leaked. The filter runs on every record, before any formatter, and rewrites
the message and every structured field. It is a safety net, not a licence to log secrets: code should still avoid it.
"""

import logging
import re
from typing import Any

MASK = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWTs (header.payload.signature, base64url) — access tokens in headers, URLs or error messages.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"), MASK),
    # Authorization headers of any scheme.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + MASK),
    # Provider API keys (Anthropic, OpenAI-style, GitHub, AWS access key ids).
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    # Passwords inside connection strings: scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+@"), r"\1" + MASK + "@"),
    # key=value / "key": "value" pairs whose key names a secret.
    (
        re.compile(
            r"""(?ix)
            (["']?\b(?:password|passwd|pwd|secret|token|refresh_token|access_token|api[_-]?key|authorization|cookie)\b
            ["']?\s*[:=]\s*["']?)
            ([^"'\s,;&}]+)
            """
        ),
        r"\1" + MASK,
    ),
]
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
SECRET_KEYS = {"password", "secret", "token", "authorization", "cookie", "api_key", "refresh_token", "access_token"}


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    # Personal data: keep the first character and the domain, enough to correlate without identifying anyone.
    return _EMAIL.sub(r"\1***@\2", text)


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in SECRET_KEYS:
        return MASK
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(key, v) for v in value)
    return value


_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class RedactingFilter(logging.Filter):
    """Attach to a handler: rewrites the formatted message and structured extras of every record in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a broken format string must not drop the record
            message = str(record.msg)
        record.msg, record.args = redact(message), None
        for key, value in list(vars(record).items()):
            if key not in _STANDARD:
                setattr(record, key, _redact_value(key, value))
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
