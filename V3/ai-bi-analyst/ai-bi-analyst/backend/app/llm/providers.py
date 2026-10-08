"""One internal interface, three implementations.

Provider-specific request construction, structured-output handling, timeouts,
retry-on-rate-limit and token-usage extraction are isolated per class; callers
only ever see an `LLMResult`.

`heuristic` is a real provider, not a stub: it produces valid structured output
from the computed profile so the whole product runs with no API key at all.
Keys are read from the environment on the server; they are never returned to the
client and never written to logs.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings
from .usage import (
    TokenUsage,
    extract_anthropic_usage,
    extract_openai_usage,
    new_request_id,
)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class LLMError(RuntimeError):
    """Provider failure, with a message safe to show to a user."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class LLMResult:
    """Parsed JSON plus the provider's own account of what it cost."""

    data: dict[str, Any]
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    request_id: str = field(default_factory=new_request_id)


class LLMProvider(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        """Return parsed JSON plus reported usage. Raise LLMError on failure."""

    @property
    def available(self) -> bool:
        return True


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"The model returned malformed JSON: {exc.msg}.") from exc
    raise LLMError("The model returned no JSON object.")


def _cut_off(label: str, max_tokens: int) -> LLMError:
    """A reply stopped at the output limit is half a JSON document.

    Said plainly, and worded without "JSON" so the service does not spend a
    repair attempt re-asking for a reply that will stop at the same limit.
    """
    return LLMError(f"{label} reached the {max_tokens:,}-token output limit before finishing the reply.")


def _post_with_retry(url: str, *, headers: dict[str, str], payload: dict[str, Any], label: str) -> dict[str, Any]:
    """One place for timeouts, rate-limit backoff and error shaping."""
    last: LLMError | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=settings.llm_timeout_seconds)
        except httpx.TimeoutException as exc:
            last = LLMError(f"{label} timed out after {settings.llm_timeout_seconds}s.", retryable=True)
            if attempt == MAX_ATTEMPTS:
                raise last from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{label} request failed: {type(exc).__name__}.") from exc
        else:
            if response.status_code in RETRYABLE_STATUS:
                last = LLMError(
                    f"{label} returned HTTP {response.status_code}.",
                    retryable=True,
                    status=response.status_code,
                )
                if attempt == MAX_ATTEMPTS:
                    raise last
            elif response.status_code >= 400:
                detail = "rate limited" if response.status_code == 429 else "rejected the request"
                raise LLMError(f"{label} {detail} (HTTP {response.status_code}).", status=response.status_code)
            else:
                try:
                    return response.json()
                except ValueError as exc:
                    raise LLMError(f"{label} returned a non-JSON body.") from exc
        time.sleep(min(2**attempt * 0.5, 4.0))
    raise last or LLMError(f"{label} could not be reached.")


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._key = api_key or settings.openai_api_key
        self._model = model or settings.openai_model

    @property
    def model(self) -> str:
        return self._model

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        if not self._key:
            raise LLMError("No OpenAI API key is configured on the server.")
        payload = {
            "model": self._model,
            "temperature": settings.llm_temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = _post_with_retry(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            payload=payload,
            label="OpenAI",
        )
        if data["choices"][0].get("finish_reason") == "length":
            raise _cut_off("OpenAI", max_tokens)
        usage, reported_model = extract_openai_usage(data)
        return LLMResult(
            data=extract_json(data["choices"][0]["message"]["content"]),
            usage=usage,
            model=reported_model or self._model,
            request_id=str(data.get("id") or new_request_id())[:64],
        )


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._key = api_key or settings.anthropic_api_key
        self._model = model or settings.anthropic_model

    @property
    def model(self) -> str:
        return self._model

    @property
    def available(self) -> bool:
        return bool(self._key)

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        if not self._key:
            raise LLMError("No Anthropic API key is configured on the server.")
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": settings.llm_temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = _post_with_retry(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
            label="Anthropic",
        )
        if data.get("stop_reason") == "max_tokens":
            raise _cut_off("Anthropic", max_tokens)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage, reported_model = extract_anthropic_usage(data)
        return LLMResult(
            data=extract_json(text),
            usage=usage,
            model=reported_model or self._model,
            request_id=str(data.get("id") or new_request_id())[:64],
        )


class HeuristicProvider(LLMProvider):
    """Deterministic fallback. Wording is templated from computed facts only."""

    name = "heuristic"

    @property
    def model(self) -> str:
        return "rule-based-narrator"

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> LLMResult:
        raise LLMError("The heuristic provider does not call a model. The service composes its output directly.")


def get_provider(name: str | None = None, session_key: str | None = None) -> LLMProvider:
    choice = (name or settings.llm_provider or "heuristic").lower()
    if choice == "openai":
        provider = OpenAIProvider(api_key=session_key)
    elif choice == "anthropic":
        provider = AnthropicProvider(api_key=session_key)
    else:
        return HeuristicProvider()
    if not provider.available:
        return HeuristicProvider()
    return provider


def available_providers() -> list[dict[str, Any]]:
    """What the UI is allowed to offer. Never includes key material."""
    return [
        {
            "id": "heuristic",
            "label": "Built-in rule-based narrator (no API key, fully offline)",
            "configured": True,
            "model": "rule-based-narrator",
        },
        {
            "id": "openai",
            "label": "OpenAI",
            "configured": bool(settings.openai_api_key),
            "model": settings.openai_model,
        },
        {
            "id": "anthropic",
            "label": "Anthropic",
            "configured": bool(settings.anthropic_api_key),
            "model": settings.anthropic_model,
        },
    ]
