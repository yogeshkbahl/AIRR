"""The deterministic pipeline: ingest -> type -> profile -> anomalies -> relationships."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from ..schemas import DatasetOverview, DatasetStatus
from .anomalies import detect_anomalies
from .ingestion import IngestionInfo
from .profiling import build_column_profile, build_overview
from .relationships import build_matrix
from .semantics import apply_overrides, classify_column
from .store import Session


def build_session(
    dataset_id: str,
    df: pd.DataFrame,
    info: IngestionInfo,
    business_context: str,
    overrides: dict[str, dict] | None = None,
    with_relationships: bool = True,
) -> Session:
    semantics = [
        classify_column(df[col], col, position=i, total_rows=len(df))
        for i, col in enumerate(df.columns)
    ]
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

    return Session(
        id=dataset_id,
        filename=info.filename,
        created_at=datetime.now(timezone.utc),
        df=df,
        semantics=semantics,
        columns=profiles,
        overview=overview,
        anomalies=anomalies,
        relationships=relationships,
        status=DatasetStatus(dataset_id=dataset_id, state="ready", progress=1.0, message="Profiling complete"),
        overrides=overrides or {},
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
    rebuilt = build_session(
        session.id, session.df, info, session.overview.business_context, merged
    )
    rebuilt.storyboard = session.storyboard
    return rebuilt
