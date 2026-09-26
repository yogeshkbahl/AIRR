"""Session workspace.

Each upload becomes an isolated session: its own directory, its own in-memory
frame, its own metadata row. Swapping this module for object storage plus
PostgreSQL is the only change needed to run multi-node.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import settings
from ..schemas import (
    Anomaly,
    AuditRecord,
    ColumnProfile,
    DatasetOverview,
    DatasetStatus,
    RelationshipResult,
    Storyboard,
)
from .semantics import ColumnSemantics

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    row_count INTEGER,
    column_count INTEGER,
    state TEXT NOT NULL,
    business_context TEXT,
    sampled INTEGER DEFAULT 0,
    overview_json TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    profile_version TEXT,
    sampled INTEGER DEFAULT 0,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS storyboards (
    dataset_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS column_overrides (
    dataset_id TEXT NOT NULL,
    column_name TEXT NOT NULL,
    patch TEXT NOT NULL,
    PRIMARY KEY (dataset_id, column_name)
);
"""


@dataclass
class Session:
    id: str
    filename: str
    created_at: datetime
    df: pd.DataFrame
    semantics: list[ColumnSemantics]
    columns: list[ColumnProfile]
    overview: DatasetOverview
    anomalies: list[Anomaly] = field(default_factory=list)
    relationships: list[RelationshipResult] = field(default_factory=list)
    storyboard: Storyboard | None = None
    status: DatasetStatus | None = None
    overrides: dict[str, dict] = field(default_factory=dict)
    sensitive_columns: set[str] = field(default_factory=set)

    @property
    def dir(self) -> Path:
        return settings.workspace_dir / self.id


class Store:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        settings.ensure_dirs()
        with self._connect() as con:
            con.executescript(SCHEMA)

    # ---------------- sqlite ----------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(settings.metadata_db, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    # ---------------- lifecycle ----------------

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:16]

    def session_dir(self, dataset_id: str) -> Path:
        path = settings.workspace_dir / dataset_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def put(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.id] = session
            session.sensitive_columns = {s.name for s in session.semantics if s.is_sensitive}
        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO datasets
                   (id, filename, created_at, row_count, column_count, state, business_context, sampled, overview_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    session.id,
                    session.filename,
                    session.created_at.isoformat(),
                    session.overview.row_count,
                    session.overview.column_count,
                    "ready",
                    session.overview.business_context,
                    int(session.overview.sampled),
                    session.overview.model_dump_json(),
                ),
            )
        try:
            session.df.to_parquet(self.session_dir(session.id) / "data.parquet", index=False)
        except Exception:
            # Parquet is a convenience for restart; analysis continues from memory.
            pass

    def get(self, dataset_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(dataset_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, filename, created_at, row_count, column_count, state FROM datasets ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        live = set(self._sessions)
        return [
            {**dict(r), "loaded": r["id"] in live}
            for r in rows
        ]

    def delete(self, dataset_id: str) -> bool:
        with self._lock:
            existed = self._sessions.pop(dataset_id, None) is not None
        path = settings.workspace_dir / dataset_id
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            existed = True
        with self._connect() as con:
            con.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
            con.execute("DELETE FROM audit WHERE dataset_id = ?", (dataset_id,))
            con.execute("DELETE FROM storyboards WHERE dataset_id = ?", (dataset_id,))
            con.execute("DELETE FROM column_overrides WHERE dataset_id = ?", (dataset_id,))
        return existed

    def purge_expired(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.session_retention_hours)
        removed = 0
        with self._connect() as con:
            rows = con.execute("SELECT id, created_at FROM datasets").fetchall()
        for row in rows:
            try:
                created = datetime.fromisoformat(row["created_at"])
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                self.delete(row["id"])
                removed += 1
        return removed

    # ---------------- audit ----------------

    def audit(self, record: AuditRecord) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO audit (dataset_id, at, action, provider, model, prompt_version, profile_version, sampled, detail)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    record.dataset_id,
                    record.at.isoformat(),
                    record.action,
                    record.provider,
                    record.model,
                    record.prompt_version,
                    record.profile_version,
                    int(record.sampled),
                    record.detail[:2000],
                ),
            )

    def audit_trail(self, dataset_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT at, action, provider, model, prompt_version, profile_version, sampled, detail "
                "FROM audit WHERE dataset_id = ? ORDER BY id DESC LIMIT 200",
                (dataset_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- storyboard ----------------

    def save_storyboard(self, board: Storyboard) -> Storyboard:
        board.updated_at = datetime.now(timezone.utc)
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO storyboards (dataset_id, updated_at, payload) VALUES (?,?,?)",
                (board.dataset_id, board.updated_at.isoformat(), board.model_dump_json()),
            )
        session = self.get(board.dataset_id)
        if session:
            session.storyboard = board
        return board

    def load_storyboard(self, dataset_id: str) -> Storyboard | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT payload FROM storyboards WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        if not row:
            return None
        return Storyboard.model_validate_json(row["payload"])

    # ---------------- overrides ----------------

    def save_overrides(self, dataset_id: str, overrides: dict[str, dict]) -> None:
        with self._connect() as con:
            for name, patch in overrides.items():
                con.execute(
                    "INSERT OR REPLACE INTO column_overrides (dataset_id, column_name, patch) VALUES (?,?,?)",
                    (dataset_id, name, json.dumps(patch)),
                )


store = Store()
