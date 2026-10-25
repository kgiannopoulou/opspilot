"""The single place OpsPilot talks to a large language model.

Only structured (JSON-schema constrained) calls are exposed: every LLM output in OpsPilot is parsed, validated and
checked against the evidence before anyone sees it, so free-form prose never leaves this module.
"""

from __future__ import annotations

import json
import logging
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


@lru_cache
def get_llm() -> LLMClient | None:
    """The configured model, or None: callers then use their deterministic, non-LLM path."""
    settings = get_settings()
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "anthropic":
        return ClaudeClient(settings.llm_model, settings.llm_effort, settings.llm_timeout_seconds)
    raise ValueError(f"unknown LLM provider {settings.llm_provider!r}")
