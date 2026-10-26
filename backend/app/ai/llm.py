"""The single place OpsPilot talks to a large language model.

Only structured (JSON-schema constrained) calls are exposed: every LLM output in OpsPilot is parsed, validated and
checked against the evidence before anyone sees it, so free-form prose never leaves this module.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The model could not produce a usable answer (unavailable, refused, truncated, invalid JSON)."""


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4096) -> dict[str, Any]:
        """Return the model's answer as a dict that conforms to `schema`, or raise LLMError."""
        ...


class ClaudeClient:
    """Claude via the official Anthropic SDK. Credentials come from the environment (ANTHROPIC_API_KEY)."""

    def __init__(self, model: str, effort: str, timeout: float, client: Any = None) -> None:
        import anthropic

        self.anthropic = anthropic
        self.model = model
        self.effort = effort
        self.name = model
        self.client = client or anthropic.Anthropic(timeout=timeout, max_retries=2)

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4096) -> dict[str, Any]:
        anthropic = self.anthropic
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                # On a safety decline, let the API re-run the request on a fallback model instead of failing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMError("rate limited by the model provider") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"model provider returned HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("model provider unreachable") from exc

        if response.stop_reason == "refusal":
            raise LLMError("the model declined to answer")
        if response.stop_reason == "max_tokens":
            raise LLMError("the answer was truncated (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("the model returned no text block")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("the model returned invalid JSON") from exc
        log.info("llm call model=%s request_id=%s usage=%s", self.model, response._request_id, response.usage)
        return data


@dataclass
class CircuitState:
    state: str  # "closed" (calls go through) | "open" (failing fast) | "half_open" (one trial call allowed)
    consecutive_failures: int
    open_until: float | None
    last_error: str | None


class CircuitBreaker:
    """Stop calling a provider that keeps failing.

    Without it, every investigation during a provider outage waits for the full timeout (and SDK retries) before
    falling back. After `threshold` consecutive failures the circuit opens and calls fail immediately with LLMError —
    callers take their non-LLM path at once. After `cooldown` seconds one trial call is let through (half-open): success
    closes the circuit, failure opens it again. State is per process (API and each worker decide independently).
    """

    def __init__(self, threshold: int = 3, cooldown: float = 60.0, clock: Any = time.monotonic) -> None:
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.failures, self.opened_at, self.last_error = 0, None, None
        self.lock = threading.Lock()

    def state(self) -> CircuitState:
        with self.lock:
            if self.opened_at is None:
                return CircuitState("closed", self.failures, None, self.last_error)
            until = self.opened_at + self.cooldown
            return CircuitState("open" if self.clock() < until else "half_open", self.failures, until, self.last_error)

    def before_call(self) -> None:
        state = self.state()
        if state.state == "open":
            raise LLMError(f"LLM circuit open after {state.consecutive_failures} consecutive failures "
                           f"(last: {state.last_error}); using the non-LLM path")  # fmt: skip
        if state.state == "half_open":
            with self.lock:
                self.opened_at = self.clock()  # one trial: concurrent callers keep failing fast meanwhile

    def success(self) -> None:
        with self.lock:
            self.failures, self.opened_at, self.last_error = 0, None, None

    def failure(self, error: str) -> None:
        with self.lock:
            self.failures += 1
            self.last_error = error
            if self.failures >= self.threshold:
                self.opened_at = self.clock()


class GuardedLLM:
    """Any LLMClient behind a circuit breaker. Unexpected exceptions are normalised to LLMError for callers."""

    def __init__(self, client: LLMClient, breaker: CircuitBreaker | None = None) -> None:
        self.client = client
        self.breaker = breaker or CircuitBreaker()
        self.name = client.name

    def complete_json(self, system: str, prompt: str, schema: dict[str, Any], max_tokens: int = 4096) -> dict[str, Any]:
        self.breaker.before_call()
        try:
            data = self.client.complete_json(system, prompt, schema, max_tokens)
        except LLMError as exc:
            self.breaker.failure(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 — a timeout or SDK bug must not escape as a 500
            self.breaker.failure(type(exc).__name__)
            raise LLMError(f"model call failed: {type(exc).__name__}") from exc
        self.breaker.success()
        return data


@lru_cache
def get_llm() -> LLMClient | None:
    """The configured model behind a circuit breaker, or None: callers then use their deterministic, non-LLM path."""
    settings = get_settings()
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "anthropic":
        return GuardedLLM(ClaudeClient(settings.llm_model, settings.llm_effort, settings.llm_timeout_seconds))
    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")


def llm_status() -> dict[str, Any]:
    """For /ai/status: is the model configured, and is its circuit closed?"""
    settings = get_settings()
    llm = get_llm()
    state = llm.breaker.state() if isinstance(llm, GuardedLLM) else None
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model if llm else None,
        "circuit": state.state if state else None,
        "consecutive_failures": state.consecutive_failures if state else 0,
        "last_error": state.last_error if state else None,
    }
