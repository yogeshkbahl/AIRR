"""One internal interface, three implementations.

`heuristic` is a real provider, not a stub: it produces valid structured output
from the computed profile so the whole product runs with no API key at all.
Keys are read from the environment on the server; they are never returned to the
client and never written to logs.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import settings


class LLMError(RuntimeError):
    """Provider failure, with a message safe to show to a user."""


class LLMProvider(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> dict[str, Any]:
        """Return parsed JSON. Raise LLMError on failure."""

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

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> dict[str, Any]:
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
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json=payload,
                timeout=settings.llm_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI request failed: {type(exc).__name__}.") from exc
        if response.status_code >= 400:
            raise LLMError(f"OpenAI returned HTTP {response.status_code}.")
        data = response.json()
        return extract_json(data["choices"][0]["message"]["content"])


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

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> dict[str, Any]:
        if not self._key:
            raise LLMError("No Anthropic API key is configured on the server.")
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": settings.llm_temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=settings.llm_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"Anthropic request failed: {type(exc).__name__}.") from exc
        if response.status_code >= 400:
            raise LLMError(f"Anthropic returned HTTP {response.status_code}.")
        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return extract_json(text)


class HeuristicProvider(LLMProvider):
    """Deterministic fallback. Wording is templated from computed facts only."""

    name = "heuristic"

    @property
    def model(self) -> str:
        return "rule-based-narrator"

    def complete_json(self, system: str, user: str, max_tokens: int = 4000) -> dict[str, Any]:
        raise LLMError(
            "The heuristic provider does not call a model. The service composes its output directly."
        )


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
