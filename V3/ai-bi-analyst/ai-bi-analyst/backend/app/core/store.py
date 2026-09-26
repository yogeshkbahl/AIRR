"""Session store on top of the dataset-scoped cache (ENH-06).

A session is an in-memory view of a dataset workspace. If the process restarts,
or a browser refreshes into a dataset that is no longer resident, the session is
rebuilt from the workspace: the original file is re-read with its recorded
parser settings, semantic overrides are re-applied, and validated artifacts are
reused instead of recomputed.

Replacing this module with object storage plus PostgreSQL is still the only
change needed to run multi-node; nothing else touches persistence.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import settings
from ..llm.usage import SessionUsage, ledger
from ..schemas import (
    Anomaly,
    AuditRecord,
    ColumnProfile,
    DatasetOverview,
    DatasetStatus,
    QualityReport,
    QualityRuleSet,
    QuickAsk,
    RelationshipResult,
    Storyboard,
    WorkspaceState,
)
from .cache import (
    ANALYSIS_CODE_VERSION,
    APP_VERSION,
    CACHE_SCHEMA_VERSION,
    DATASET_ID_RE,
    DERIVED_ARTIFACTS,
    Artifact,
    DatasetWorkspace,
    Manifest,
    overrides_fingerprint,
)
from .semantics import ColumnSemantics

log = logging.getLogger("ai_bi_analyst.store")

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
    fingerprint TEXT,
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
CREATE INDEX IF NOT EXISTS audit_dataset ON audit (dataset_id);
"""


def schema_fingerprint(semantics: list[ColumnSemantics]) -> str:
    """Identity of the semantic schema: column names, roles and aggregations."""
    parts = "|".join(f"{s.name}:{s.analytical_role.value}:{s.default_aggregation.value}" for s in semantics)
    import hashlib

    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:16]


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
    quick_asks: list[QuickAsk] = field(default_factory=list)
    workspace_state: WorkspaceState | None = None
    manifest: Manifest | None = None
    cache_source: str = "computed"

    @property
    def workspace(self) -> DatasetWorkspace:
        return DatasetWorkspace(self.id)

    @property
    def overrides_hash(self) -> str:
        return overrides_fingerprint(self.overrides)

    @property
    def schema_fingerprint(self) -> str:
        return schema_fingerprint(self.semantics)


class Store:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        settings.ensure_dirs()
        with self._connect() as con:
            con.executescript(SCHEMA)

    # ---------------- sqlite: dataset index and audit only ----------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(settings.metadata_db, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:16]

    def session_dir(self, dataset_id: str) -> Path:
        """Staging directory for an upload that has not been accepted yet."""
        path = settings.workspace_dir / dataset_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------- write ----------------

    def put(self, session: Session, *, persist_artifacts: bool = True) -> None:
        with self._lock:
            self._sessions[session.id] = session
            session.sensitive_columns = {s.name for s in session.semantics if s.is_sensitive}

        with self._connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO datasets
                   (id, filename, created_at, row_count, column_count, state, business_context,
                    sampled, fingerprint, overview_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    session.id,
                    session.filename,
                    session.created_at.isoformat(),
                    session.overview.row_count,
                    session.overview.column_count,
                    "ready",
                    session.overview.business_context,
                    int(session.overview.sampled),
                    session.manifest.sha256 if session.manifest else "",
                    session.overview.model_dump_json(),
                ),
            )

        if persist_artifacts:
            self.persist_analysis(session)

    def persist_analysis(self, session: Session) -> None:
        """Write the derived artifacts for the session's current semantics."""
        workspace = session.workspace
        if not workspace.exists:
            return
        overrides_hash = session.overrides_hash
        workspace.write_artifact(
            Artifact.overview, session.overview.model_dump(mode="json"), overrides_hash=overrides_hash
        )
        workspace.write_artifact(
            Artifact.columns,
            [c.model_dump(mode="json") for c in session.columns],
            overrides_hash=overrides_hash,
        )
        workspace.write_artifact(
            Artifact.anomalies,
            [a.model_dump(mode="json") for a in session.anomalies],
            overrides_hash=overrides_hash,
        )
        workspace.write_artifact(
            Artifact.relationships,
            [r.model_dump(mode="json") for r in session.relationships],
            overrides_hash=overrides_hash,
        )
        if session.quick_asks:
            workspace.write_artifact(
                Artifact.quick_asks,
                [q.model_dump(mode="json") for q in session.quick_asks],
                overrides_hash=overrides_hash,
            )
        if session.overrides:
            workspace.write_artifact(Artifact.semantic_overrides, session.overrides)
        workspace.update_manifest(
            overrides_hash=overrides_hash,
            analysis_timestamp=datetime.now(UTC).isoformat(),
            profile_version="profile-v1.2",
            total_rows=session.overview.row_count,
            analyzed_rows=session.overview.analyzed_row_count,
            sampled=session.overview.sampled,
            sample_method=session.overview.sample_method,
            business_context=session.overview.business_context,
        )

    def invalidate_derived(self, dataset_id: str, reason: str) -> None:
        workspace = DatasetWorkspace(dataset_id)
        if workspace.exists:
            workspace.invalidate(list(DERIVED_ARTIFACTS), reason)

    # ---------------- read ----------------

    def get(self, dataset_id: str) -> Session | None:
        if not DATASET_ID_RE.match(dataset_id or ""):
            return None
        with self._lock:
            session = self._sessions.get(dataset_id)
        if session is not None:
            return session
        return self.rehydrate(dataset_id)

    def rehydrate(self, dataset_id: str) -> Session | None:
        """Rebuild a session from its workspace after a restart or refresh."""
        from .ingestion import IngestionInfo, load_dataframe
        from .pipeline import build_session, restore_session

        workspace = DatasetWorkspace(dataset_id)
        manifest = workspace.read_manifest()
        original = workspace.original_file()
        if manifest is None or original is None:
            return None

        parser = manifest.parser or {}
        try:
            df, loaded = load_dataframe(original, manifest.original_filename, parser.get("sheet"))
        except Exception as exc:  # a workspace we cannot re-read is not usable
            log.warning("rehydrate failed dataset=%s error=%s", dataset_id, type(exc).__name__)
            return None

        info = IngestionInfo(
            filename=manifest.original_filename,
            detected_format=parser.get("format") or loaded.detected_format,
            encoding=loaded.encoding,
            delimiter=loaded.delimiter,
            sheet_name=parser.get("sheet"),
            file_size_bytes=manifest.size_bytes,
            total_rows=manifest.total_rows or loaded.total_rows,
            analyzed_rows=loaded.analyzed_rows,
            sampled=manifest.sampled or loaded.sampled,
            sample_method=manifest.sample_method or loaded.sample_method,
            notes=list(loaded.notes),
        )

        overrides = workspace.read_artifact(Artifact.semantic_overrides, require_versions=False) or {}
        overrides_hash = overrides_fingerprint(overrides)
        cached = {
            "overview": workspace.read_artifact(Artifact.overview, overrides_hash=overrides_hash),
            "columns": workspace.read_artifact(Artifact.columns, overrides_hash=overrides_hash),
            "anomalies": workspace.read_artifact(Artifact.anomalies, overrides_hash=overrides_hash),
            "relationships": workspace.read_artifact(Artifact.relationships, overrides_hash=overrides_hash),
            "quick_asks": workspace.read_artifact(Artifact.quick_asks, overrides_hash=overrides_hash),
        }

        session = None
        if cached["overview"] and cached["columns"] is not None and cached["anomalies"] is not None:
            session = restore_session(
                dataset_id, df, info, manifest.business_context, overrides=overrides, cached=cached
            )
        if session is None:
            session = build_session(dataset_id, df, info, manifest.business_context, overrides)
            session.cache_source = "rebuilt"
        log.info("rehydrate dataset=%s source=%s", dataset_id, session.cache_source)

        session.manifest = manifest
        session.created_at = _parse_iso(manifest.uploaded_at) or session.created_at
        session.storyboard = self.load_storyboard(dataset_id)
        ledger.load(dataset_id, workspace.read_artifact(Artifact.llm_usage, require_versions=False))
        session.workspace_state = self.load_workspace_state(dataset_id, session)

        self.put(session, persist_artifacts=session.cache_source != "cache")
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, filename, created_at, row_count, column_count, state "
                "FROM datasets ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        live = set(self._sessions)
        return [{**dict(r), "loaded": r["id"] in live} for r in rows]

    # ---------------- delete and retention ----------------

    def delete(self, dataset_id: str) -> bool:
        if not DATASET_ID_RE.match(dataset_id or ""):
            return False
        with self._lock:
            existed = self._sessions.pop(dataset_id, None) is not None
        ledger.forget(dataset_id)
        # The workspace holds the upload and every cache artifact together, so
        # one call removes the data and everything derived from it.
        if DatasetWorkspace(dataset_id).delete():
            existed = True
        with self._connect() as con:
            con.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
            con.execute("DELETE FROM audit WHERE dataset_id = ?", (dataset_id,))
        return existed

    def purge_expired(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=settings.session_retention_hours)
        removed = 0
        for workspace in _safe_list_workspaces():
            manifest = workspace.read_manifest()
            uploaded = _parse_iso(manifest.uploaded_at) if manifest else None
            if uploaded is None or uploaded < cutoff:
                self.delete(workspace.dataset_id)
                removed += 1
        with self._connect() as con:
            rows = con.execute("SELECT id, created_at FROM datasets").fetchall()
        for row in rows:
            created = _parse_iso(row["created_at"])
            if created is None or created < cutoff:
                self.delete(row["id"])
        return removed

    # ---------------- audit ----------------

    def audit(self, record: AuditRecord) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO audit
                   (dataset_id, at, action, provider, model, prompt_version, profile_version, sampled, detail)
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
        board.updated_at = datetime.now(UTC)
        workspace = DatasetWorkspace(board.dataset_id)
        if workspace.exists:
            workspace.write_artifact(Artifact.storyboard, board.model_dump(mode="json"))
        session = self._sessions.get(board.dataset_id)
        if session:
            session.storyboard = board
        return board

    def load_storyboard(self, dataset_id: str) -> Storyboard | None:
        payload = DatasetWorkspace(dataset_id).read_artifact(Artifact.storyboard, require_versions=False)
        if not payload:
            return None
        try:
            return Storyboard.model_validate(payload)
        except ValueError:
            log.warning("storyboard artifact invalid dataset=%s; starting a new one", dataset_id)
            return None

    # ---------------- workspace state (ENH-05) ----------------

    def save_workspace_state(self, state: WorkspaceState) -> WorkspaceState:
        state.updated_at = datetime.now(UTC)
        workspace = DatasetWorkspace(state.dataset_id)
        if workspace.exists:
            workspace.write_artifact(Artifact.workspace_state, state.model_dump(mode="json"))
        session = self._sessions.get(state.dataset_id)
        if session:
            session.workspace_state = state
        return state

    def load_workspace_state(self, dataset_id: str, session: Session | None = None) -> WorkspaceState:
        payload = DatasetWorkspace(dataset_id).read_artifact(Artifact.workspace_state, require_versions=False)
        state = WorkspaceState(dataset_id=dataset_id)
        if payload:
            try:
                candidate = WorkspaceState.model_validate(payload)
                # Never hand back state that belongs to a different file.
                if candidate.dataset_id == dataset_id:
                    state = candidate
            except ValueError:
                log.warning("workspace state artifact invalid dataset=%s", dataset_id)
        if session is not None:
            state = prune_workspace_state(state, session)
        return state

    # ---------------- governed data-quality rules ----------------

    def save_quality_rules(self, rule_set: QualityRuleSet) -> QualityRuleSet:
        rule_set.updated_at = datetime.now(UTC)
        workspace = DatasetWorkspace(rule_set.dataset_id)
        if workspace.exists:
            # Rule definitions are user configuration, so they are stored
            # without an overrides hash and survive a semantic change.
            workspace.write_artifact(Artifact.quality_rules, rule_set.model_dump(mode="json"))
        return rule_set

    def load_quality_rules(self, dataset_id: str) -> QualityRuleSet | None:
        payload = DatasetWorkspace(dataset_id).read_artifact(Artifact.quality_rules, require_versions=False)
        if not payload:
            return None
        try:
            rule_set = QualityRuleSet.model_validate(payload)
        except ValueError:
            log.warning("quality rules artifact invalid dataset=%s", dataset_id)
            return None
        return rule_set if rule_set.dataset_id == dataset_id else None

    def save_quality_report(self, dataset_id: str, report: QualityReport) -> None:
        workspace = DatasetWorkspace(dataset_id)
        if workspace.exists:
            session = self._sessions.get(dataset_id)
            workspace.write_artifact(
                Artifact.quality_results,
                report.model_dump(mode="json"),
                overrides_hash=session.overrides_hash if session else "",
            )

    def load_quality_report(self, dataset_id: str) -> QualityReport | None:
        session = self._sessions.get(dataset_id)
        payload = DatasetWorkspace(dataset_id).read_artifact(
            Artifact.quality_results,
            overrides_hash=session.overrides_hash if session else None,
        )
        if not payload:
            return None
        try:
            return QualityReport.model_validate(payload)
        except ValueError:
            return None

    # ---------------- llm usage (ENH-01) ----------------

    def save_usage(self, dataset_id: str) -> None:
        workspace = DatasetWorkspace(dataset_id)
        if workspace.exists:
            workspace.write_artifact(Artifact.llm_usage, ledger.dump(dataset_id))

    def usage(self, dataset_id: str, **kwargs: Any) -> SessionUsage:
        return ledger.summary(dataset_id, **kwargs)

    # ---------------- diagnostics ----------------

    def cache_report(self, dataset_id: str) -> dict[str, Any]:
        return DatasetWorkspace(dataset_id).cache_report()

    def storage_report(self) -> dict[str, Any]:
        workspaces = _safe_list_workspaces()
        return {
            "workspace_dir": str(settings.workspace_dir),
            "writable": _is_writable(settings.workspace_dir),
            "datasets_on_disk": len(workspaces),
            "loaded_sessions": len(self._sessions),
            "disk_bytes": sum(w.size_on_disk() for w in workspaces),
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "analysis_code_version": ANALYSIS_CODE_VERSION,
            "app_version": APP_VERSION,
            "retention_hours": settings.session_retention_hours,
        }


def prune_workspace_state(state: WorkspaceState, session: Session) -> WorkspaceState:
    """Drop anything that no longer matches the dataset's semantic schema."""
    known = {s.name for s in session.semantics}
    selected = [c for c in state.chart_advisor.selected_column_ids if c in known]
    dropped = [c for c in state.chart_advisor.selected_column_ids if c not in known]
    current = session.schema_fingerprint

    advisor = state.chart_advisor.model_copy(update={"selected_column_ids": selected})
    if state.schema_fingerprint and state.schema_fingerprint != current:
        # Roles changed under the selection: keep the columns but drop the
        # chart choice so it is re-validated against the new roles.
        advisor = advisor.model_copy(update={"active_chart_type": None})
    return state.model_copy(
        update={
            "chart_advisor": advisor,
            "schema_fingerprint": current,
            "dropped_on_restore": dropped,
        }
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _safe_list_workspaces() -> list[DatasetWorkspace]:
    from .cache import list_workspaces

    try:
        return list_workspaces()
    except OSError:
        return []


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


store = Store()
