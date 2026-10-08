"""One JSON file per question or query, for debugging.

Each run writes ``<QUERY_LOG_DIR>/<YYYY-MM-DD>/<YYYYMMDDTHHMMSS.ffffff+HHMM>_<action>_<id>.json``.
The timestamp is server-local time with microseconds and the UTC offset and the id is random, so two requests
in the same instant never share a file. Result rows are not written unless
``QUERY_LOG_INCLUDE_ROWS`` is on, because they are the user's data.

Logging must never break a request, so every failure here is swallowed and
reported through the standard logger instead.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


class QueryLog:
    """Collects one run's details; ``write()`` persists them once."""

    def __init__(self, action: str, dataset_id: str, request_id: str | None = None) -> None:
        # Local time, so the folder matches the day the user sees; the offset in
        # the name and started_at keeps it unambiguous.
        self.started = datetime.now(UTC).astimezone()
        self._t0 = time.perf_counter()
        self.log_id = uuid.uuid4().hex[:8]
        self.record: dict[str, Any] = {
            "log_id": self.log_id,
            "action": action,
            "dataset_id": dataset_id,
            "request_id": request_id,
            "started_at": self.started.isoformat(),
            "status": "ok",
        }

    def set(self, **fields: Any) -> None:
        self.record.update({k: _jsonable(v) for k, v in fields.items()})

    def error(self, exc: BaseException) -> None:
        self.record["status"] = "error"
        self.record["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            # The engine's own message (e.g. DuckDB's Binder Error text) and the
            # SQL that produced it, which the user-facing message deliberately omits.
            "detail": getattr(exc, "detail", None),
            "sql": getattr(exc, "sql", None),
            "traceback": traceback.format_exception(exc)[-8:],
        }

    def write(self) -> Path | None:
        if not settings.query_log_enabled:
            return None
        self.record["duration_ms"] = round((time.perf_counter() - self._t0) * 1000, 1)
        stamp = self.started.strftime("%Y%m%dT%H%M%S.%f%z")
        folder = settings.query_log_dir / self.started.strftime("%Y-%m-%d")
        path = folder / f"{stamp}_{self.record['action']}_{self.log_id}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.record, indent=2, default=str), encoding="utf-8")
            return path
        except OSError:
            logger.exception("Could not write query log %s", path)
            return None


def result_summary(result: Any) -> dict[str, Any] | None:
    """The parts of a PlanResult worth keeping; rows only when allowed."""
    if result is None:
        return None
    summary = {
        "sql": getattr(result, "sql_like", None),
        "columns": getattr(result, "columns", None),
        "row_count": getattr(result, "row_count", None),
        "executed_ms": getattr(result, "executed_ms", None),
    }
    if settings.query_log_include_rows:
        summary["rows"] = _jsonable((getattr(result, "rows", None) or [])[:50])
    return summary


@contextmanager
def logged(action: str, dataset_id: str, request: Any = None) -> Iterator[QueryLog]:
    """Run a block with a QueryLog that is written however the block ends."""
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    log = QueryLog(action, dataset_id, request_id)
    try:
        yield log
    except BaseException as exc:
        log.error(exc)
        raise
    finally:
        log.write()
