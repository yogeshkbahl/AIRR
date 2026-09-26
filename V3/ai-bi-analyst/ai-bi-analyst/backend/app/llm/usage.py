"""ENH-01 — provider-reported token usage, per dataset session.

Counts are only ever taken from what the provider returns. Nothing here
estimates tokens, because an estimate shown next to a real figure is worse than
no figure at all. Each call gets a backend-generated `request_id`, and the
session total is a sum over unique request ids, so a retried or replayed
frontend request cannot double count.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_HISTORY = 25


class TokenUsage(BaseModel):
    """Exactly what the provider reported. Absent fields stay None."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    @property
    def reported(self) -> bool:
        return bool(self.input_tokens or self.output_tokens or self.total_tokens)

    def normalised(self) -> TokenUsage:
        if not self.total_tokens:
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


class UsageRecord(BaseModel):
    request_id: str
    at: datetime
    action: str
    provider: str
    model: str
    prompt_version: str = ""
    ok: bool = True
    error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)


class UsageTotals(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


class SessionUsage(BaseModel):
    """What the navigation panel renders. Never contains prompts or keys."""

    dataset_id: str
    state: Literal["not_configured", "not_used", "ok", "error"] = "not_used"
    requested_provider: str | None = None
    resolved_provider: str | None = None
    resolved_model: str | None = None
    last: UsageRecord | None = None
    totals: UsageTotals = Field(default_factory=UsageTotals)
    records: list[UsageRecord] = Field(default_factory=list)
    note: str = ""
    is_session_usage: bool = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def extract_openai_usage(payload: dict[str, Any]) -> tuple[TokenUsage, str | None]:
    usage = payload.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return (
        TokenUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cached_read_tokens=prompt_details.get("cached_tokens"),
            reasoning_tokens=completion_details.get("reasoning_tokens"),
        ).normalised(),
        payload.get("model"),
    )


def extract_anthropic_usage(payload: dict[str, Any]) -> tuple[TokenUsage, str | None]:
    usage = payload.get("usage") or {}
    return (
        TokenUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_read_tokens=usage.get("cache_read_input_tokens"),
            cache_write_tokens=usage.get("cache_creation_input_tokens"),
        ).normalised(),
        payload.get("model"),
    )


class UsageLedger:
    """Per-dataset usage, deduped by request id, persisted by the caller."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, UsageRecord]] = {}

    def load(self, dataset_id: str, payload: list[dict[str, Any]] | None) -> None:
        """Restore confirmed records from the dataset cache."""
        if not payload:
            return
        with self._lock:
            bucket = self._records.setdefault(dataset_id, {})
            for item in payload:
                try:
                    record = UsageRecord.model_validate(item)
                except ValueError:
                    continue
                bucket.setdefault(record.request_id, record)

    def record(self, dataset_id: str, record: UsageRecord) -> UsageRecord:
        with self._lock:
            bucket = self._records.setdefault(dataset_id, {})
            # Dedupe: the same backend request id is never counted twice.
            bucket.setdefault(record.request_id, record)
            trimmed = sorted(bucket.values(), key=lambda r: r.at)[-MAX_HISTORY:]
            self._records[dataset_id] = {r.request_id: r for r in trimmed}
            return bucket[record.request_id]

    def dump(self, dataset_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                record.model_dump(mode="json")
                for record in sorted(self._records.get(dataset_id, {}).values(), key=lambda r: r.at)
            ]

    def forget(self, dataset_id: str) -> None:
        with self._lock:
            self._records.pop(dataset_id, None)

    def summary(
        self,
        dataset_id: str,
        *,
        requested_provider: str | None = None,
        resolved_provider: str | None = None,
        resolved_model: str | None = None,
        provider_configured: bool = True,
    ) -> SessionUsage:
        with self._lock:
            records = sorted(self._records.get(dataset_id, {}).values(), key=lambda r: r.at)

        totals = UsageTotals()
        for record in records:
            if not record.ok or not record.usage.reported:
                continue
            totals.requests += 1
            totals.input_tokens += record.usage.input_tokens
            totals.output_tokens += record.usage.output_tokens
            totals.total_tokens += record.usage.total_tokens
            totals.cached_read_tokens += record.usage.cached_read_tokens or 0
            totals.cache_write_tokens += record.usage.cache_write_tokens or 0
            totals.reasoning_tokens += record.usage.reasoning_tokens or 0

        last = records[-1] if records else None
        # A failed request must not erase the confirmed total, so the last
        # successful record stays visible alongside the failure state.
        last_ok = next((r for r in reversed(records) if r.ok and r.usage.reported), None)

        if not provider_configured:
            state: Literal["not_configured", "not_used", "ok", "error"] = "not_configured"
            note = (
                f"{requested_provider} has no server-side key, so the built-in narrator answered. "
                "Set the key in the backend environment to use it."
            )
        elif resolved_provider == "heuristic":
            state = "not_used"
            note = "Built-in rule-based narrator: no model call, so no tokens are consumed."
        elif last is None:
            state = "not_used"
            note = "Not used yet for this dataset."
        elif not last.ok:
            state = "error"
            note = f"The last request failed ({last.error}). Totals below are the last confirmed figures."
        else:
            state = "ok"
            note = "Session usage for this dataset only. Not billing data."

        return SessionUsage(
            dataset_id=dataset_id,
            state=state,
            requested_provider=requested_provider,
            resolved_provider=resolved_provider,
            resolved_model=(last_ok.model if last_ok else resolved_model),
            last=last,
            totals=totals,
            records=records[-10:],
            note=note,
        )


def build_record(
    *,
    action: str,
    provider: str,
    model: str,
    prompt_version: str,
    usage: TokenUsage | None = None,
    ok: bool = True,
    error: str | None = None,
    request_id: str | None = None,
) -> UsageRecord:
    return UsageRecord(
        request_id=request_id or new_request_id(),
        at=datetime.now(UTC),
        action=action,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        ok=ok,
        error=error,
        usage=(usage or TokenUsage()).normalised(),
    )


ledger = UsageLedger()
