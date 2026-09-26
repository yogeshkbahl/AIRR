"""The deterministic pipeline: ingest -> type -> profile -> anomalies -> relationships.

`build_session` computes everything. `restore_session` rebuilds the same object
from validated cache artifacts, recomputing only the parts that cannot be
serialised (the typed column views used by the query engine).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..schemas import (
    Anomaly,
    ColumnProfile,
    DatasetOverview,
    DatasetStatus,
    QuickAsk,
    RelationshipResult,
)
from .anomalies import detect_anomalies
from .ingestion import IngestionInfo
from .profiling import build_column_profile, build_overview
from .quick_asks import generate_quick_asks
from .relationships import build_matrix
from .semantics import apply_overrides, classify_column
from .store import Session

log = logging.getLogger("ai_bi_analyst.pipeline")


def build_session(
    dataset_id: str,
    df: pd.DataFrame,
    info: IngestionInfo,
    business_context: str,
    overrides: dict[str, dict] | None = None,
    with_relationships: bool = True,
) -> Session:
    semantics = [classify_column(df[col], col, position=i, total_rows=len(df)) for i, col in enumerate(df.columns)]
    if overrides:
        semantics = apply_overrides(semantics, overrides)

    profiles = [build_column_profile(df, sem, len(df)) for sem in semantics]
    anomalies = detect_anomalies(df, semantics)
    overview = DatasetOverview.model_validate(
        build_overview(
            dataset_id,
            df,
            profiles,
            filename=info.filename,
            file_size_bytes=info.file_size_bytes,
            total_rows=info.total_rows or len(df),
            analyzed_rows=len(df),
            sampled=info.sampled,
            sample_method=info.sample_method,
            business_context=business_context,
            notes=info.notes,
            anomalies=anomalies,
        )
    )
    relationships = []
    if with_relationships:
        relationships = build_matrix(df, semantics).pairs[:40]

    quick_asks = generate_quick_asks(semantics, len(df))

    return Session(
        id=dataset_id,
        filename=info.filename,
        created_at=datetime.now(UTC),
        df=df,
        semantics=semantics,
        columns=profiles,
        overview=overview,
        anomalies=anomalies,
        relationships=relationships,
        status=DatasetStatus(dataset_id=dataset_id, state="ready", progress=1.0, message="Profiling complete"),
        overrides=overrides or {},
        quick_asks=quick_asks,
        cache_source="computed",
    )


def restore_session(
    dataset_id: str,
    df: pd.DataFrame,
    info: IngestionInfo,
    business_context: str,
    *,
    overrides: dict[str, dict] | None,
    cached: dict[str, Any],
) -> Session | None:
    """Rebuild a session from cache artifacts, or return None if they do not fit.

    Column semantics are always recomputed because they carry parsed datetime
    and numeric views that the query engine needs and that are not worth
    serialising. Everything expensive and purely derived comes from the cache.
    """
    semantics = [classify_column(df[col], col, position=i, total_rows=len(df)) for i, col in enumerate(df.columns)]
    if overrides:
        semantics = apply_overrides(semantics, overrides)

    try:
        overview = DatasetOverview.model_validate(cached["overview"])
        columns = [ColumnProfile.model_validate(c) for c in cached["columns"]]
        anomalies = [Anomaly.model_validate(a) for a in cached["anomalies"]]
        relationships = [RelationshipResult.model_validate(r) for r in (cached.get("relationships") or [])]
        quick_asks = [QuickAsk.model_validate(q) for q in (cached.get("quick_asks") or [])]
    except ValueError as exc:
        log.warning("cached artifacts did not validate dataset=%s error=%s", dataset_id, type(exc).__name__)
        return None

    cached_names = {c.name for c in columns}
    if cached_names != {s.name for s in semantics}:
        # The file behind the cache no longer has the same columns.
        log.warning("cached columns do not match the file dataset=%s", dataset_id)
        return None

    if not quick_asks:
        quick_asks = generate_quick_asks(semantics, len(df))

    return Session(
        id=dataset_id,
        filename=info.filename,
        created_at=datetime.now(UTC),
        df=df,
        semantics=semantics,
        columns=columns,
        overview=overview,
        anomalies=anomalies,
        relationships=relationships,
        status=DatasetStatus(
            dataset_id=dataset_id, state="ready", progress=1.0, message="Restored from the dataset cache"
        ),
        overrides=overrides or {},
        quick_asks=quick_asks,
        cache_source="cache",
    )


def reprofile(session: Session, overrides: dict[str, dict]) -> Session:
    """Rebuild a session after the user overrides column roles."""
    merged = {**session.overrides, **overrides}
    info = IngestionInfo(
        filename=session.filename,
        detected_format="cached",
        file_size_bytes=session.overview.file_size_bytes,
        total_rows=session.overview.row_count,
        analyzed_rows=session.overview.analyzed_row_count,
        sampled=session.overview.sampled,
        sample_method=session.overview.sample_method,
        notes=list(session.overview.ingestion_notes),
    )
    rebuilt = build_session(session.id, session.df, info, session.overview.business_context, merged)
    rebuilt.storyboard = session.storyboard
    return rebuilt
