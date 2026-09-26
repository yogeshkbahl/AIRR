"""Deterministic profiling. Nothing here calls an LLM."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..config import settings
from ..schemas import (
    Aggregation,
    AnalyticalRole,
    CategoricalStats,
    ColumnProfile,
    DateStats,
    NumericStats,
    PhysicalType,
    QualityComponent,
    QualityScore,
    TopValue,
)
from .semantics import ColumnSemantics, safe_float
from .sensitive import mask_series_samples

PROFILE_VERSION = "profile-v1.2"


def _numeric_stats(values: pd.Series) -> NumericStats:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return NumericStats()
    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    lo, hi = q1 - settings.iqr_multiplier * iqr, q3 + settings.iqr_multiplier * iqr
    iqr_outliers = int(((clean < lo) | (clean > hi)).sum()) if iqr > 0 else 0

    median = float(clean.median())
    mad = float((clean - median).abs().median())
    if mad > 0:
        robust_z = 0.6745 * (clean - median) / mad
        rz_outliers = int((robust_z.abs() > settings.robust_z_threshold).sum())
    else:
        rz_outliers = 0

    bins = min(30, max(5, int(np.sqrt(len(clean)))))
    counts, edges = np.histogram(clean.to_numpy(dtype=float), bins=bins)
    return NumericStats(
        min=safe_float(clean.min()),
        max=safe_float(clean.max()),
        mean=safe_float(clean.mean()),
        median=safe_float(median),
        std=safe_float(clean.std(ddof=1)) if len(clean) > 1 else 0.0,
        p25=safe_float(q1),
        p75=safe_float(q3),
        iqr=safe_float(iqr),
        zero_count=int((clean == 0).sum()),
        negative_count=int((clean < 0).sum()),
        skew=safe_float(clean.skew()) if len(clean) > 2 else None,
        outlier_count_iqr=iqr_outliers,
        outlier_count_robust_z=rz_outliers,
        histogram_bins=[round(float(e), 6) for e in edges],
        histogram_counts=[int(c) for c in counts],
    )


def casing_variant_groups(values: pd.Series) -> int:
    text = values.dropna().astype(str)
    if text.empty:
        return 0
    folded = text.str.strip().str.casefold()
    grouped = pd.DataFrame({"raw": text.str.strip(), "folded": folded}).drop_duplicates()
    per_group = grouped.groupby("folded")["raw"].nunique()
    return int((per_group > 1).sum())


def _categorical_stats(values: pd.Series, non_null: int) -> CategoricalStats:
    text = values.dropna().astype(str)
    if text.empty:
        return CategoricalStats()
    counts = text.value_counts()
    total = int(counts.sum())
    top = [
        TopValue(value=str(idx)[:120], count=int(cnt), percent=round(100 * cnt / total, 2))
        for idx, cnt in counts.head(10).items()
    ]
    rare_mask = counts / total < settings.rare_category_threshold
    return CategoricalStats(
        top_values=top,
        rare_category_count=int(rare_mask.sum()),
        rare_category_rate=round(float(counts[rare_mask].sum() / total), 4) if total else 0.0,
        blank_string_count=int((text.str.strip() == "").sum()),
        whitespace_issue_count=int((text != text.str.strip()).sum()),
        casing_variant_groups=casing_variant_groups(values),
        mean_length=safe_float(text.str.len().mean()),
        max_length=int(text.str.len().max()),
    )


def infer_granularity(dt: pd.Series) -> str | None:
    clean = dt.dropna().sort_values()
    if len(clean) < 3:
        return None
    diffs = clean.diff().dropna().dt.total_seconds()
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return "single point in time"
    median = float(diffs.median())
    for seconds, label in (
        (60, "second"),
        (3600, "minute"),
        (86400, "hour"),
        (86400 * 6, "day"),
        (86400 * 25, "week"),
        (86400 * 80, "month"),
        (86400 * 200, "quarter"),
    ):
        if median < seconds:
            return label
    return "year"


def _date_stats(parsed: pd.Series, original: pd.Series) -> DateStats:
    clean = parsed.dropna()
    if clean.empty:
        return DateStats(invalid_count=int(original.notna().sum()))
    now = pd.Timestamp.now()
    daily = clean.dt.floor("D")
    distinct_days = int(daily.nunique())
    gaps = daily.drop_duplicates().sort_values().diff().dropna().dt.days
    return DateStats(
        earliest=clean.min().isoformat(),
        latest=clean.max().isoformat(),
        span_days=int((clean.max() - clean.min()).days),
        invalid_count=int((original.notna() & parsed.isna()).sum()),
        future_count=int((clean > now).sum()),
        distinct_days=distinct_days,
        inferred_granularity=infer_granularity(clean),
        largest_gap_days=int(gaps.max()) if not gaps.empty else 0,
    )


def build_column_profile(df: pd.DataFrame, sem: ColumnSemantics, analyzed_rows: int) -> ColumnProfile:
    series = df[sem.name]
    profile = ColumnProfile(
        name=sem.name,
        label=sem.label,
        position=sem.position,
        physical_type=sem.physical_type,
        analytical_role=sem.analytical_role,
        role_reason=sem.reason,
        default_aggregation=sem.default_aggregation,
        non_null_count=sem.non_null_count,
        null_count=sem.null_count,
        null_percent=round(100 * sem.null_count / analyzed_rows, 3) if analyzed_rows else 0.0,
        distinct_count=sem.distinct_count,
        uniqueness_ratio=sem.uniqueness_ratio,
        is_sensitive=sem.is_sensitive,
        sensitive_kind=sem.sensitive_kind,
        hierarchy=sem.hierarchy,
        hierarchy_level=sem.hierarchy_level,
    )

    if sem.physical_type in (PhysicalType.integer, PhysicalType.float, PhysicalType.decimal):
        profile.numeric = _numeric_stats(series)
    if sem.physical_type in (PhysicalType.string, PhysicalType.boolean) or sem.analytical_role in (
        AnalyticalRole.categorical_dimension,
        AnalyticalRole.geography,
    ):
        profile.categorical = _categorical_stats(series.astype("object"), sem.non_null_count)
    if sem.physical_type in (PhysicalType.date, PhysicalType.datetime_):
        parsed = sem.parsed_datetime if sem.parsed_datetime is not None else pd.to_datetime(series, errors="coerce")
        profile.date = _date_stats(parsed, series)

    if sem.is_sensitive:
        profile.sample_values = mask_series_samples(series, sem.sensitive_kind)
    else:
        profile.sample_values = [str(v)[:80] for v in series.dropna().unique()[:5]]
    return profile


def quality_score(
    df: pd.DataFrame,
    profiles: list[ColumnProfile],
    duplicate_percent: float,
    missing_percent: float,
    anomalies: list | None = None,
) -> QualityScore:
    """Weighted, fully explainable. Each component is 0-100."""
    completeness = max(0.0, 100.0 - missing_percent)
    uniqueness = max(0.0, 100.0 - duplicate_percent * 2)

    usable = [p for p in profiles if p.analytical_role != AnalyticalRole.unusable]
    typing = 100.0 * len(usable) / len(profiles) if profiles else 0.0

    rows = max(1, len(df))
    outliers = sum((p.numeric.outlier_count_iqr if p.numeric else 0) for p in profiles)

    # Scored per column and then averaged, so a single dirty column is visible
    # instead of being diluted by the size of the table.
    consistency_scores: list[float] = []
    for p in profiles:
        issues = 0
        if p.categorical:
            issues += p.categorical.whitespace_issue_count + p.categorical.blank_string_count
            issues += p.categorical.casing_variant_groups * max(1, int(0.02 * rows))
        if p.date:
            issues += p.date.invalid_count + p.date.future_count
        consistency_scores.append(max(0.0, 100.0 - 100.0 * issues / rows))
    consistency = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 100.0

    validity_scores = [
        max(0.0, 100.0 - 300.0 * p.numeric.outlier_count_iqr / max(1, p.non_null_count))
        for p in profiles
        if p.numeric is not None
    ]
    validity = sum(validity_scores) / len(validity_scores) if validity_scores else 100.0

    anomalies = anomalies or []
    high = sum(1 for a in anomalies if a.severity.value == "high")
    medium = sum(1 for a in anomalies if a.severity.value == "medium")
    low = sum(1 for a in anomalies if a.severity.value == "low")
    findings = max(0.0, 100.0 - (10 * high + 4 * medium + 1 * low))

    components = [
        QualityComponent(
            name="Completeness",
            weight=0.30,
            score=round(completeness, 2),
            detail=f"100 − missing cell percentage ({missing_percent:.2f}%).",
        ),
        QualityComponent(
            name="Row uniqueness",
            weight=0.15,
            score=round(uniqueness, 2),
            detail=f"100 − 2 × duplicate row percentage ({duplicate_percent:.2f}%).",
        ),
        QualityComponent(
            name="Type usability",
            weight=0.10,
            score=round(typing, 2),
            detail=f"{len(usable)} of {len(profiles)} columns have a usable analytical role.",
        ),
        QualityComponent(
            name="Value consistency",
            weight=0.15,
            score=round(consistency, 2),
            detail="Average per column: penalises blank strings, stray whitespace, casing variants, "
            "invalid and future dates.",
        ),
        QualityComponent(
            name="Numeric validity",
            weight=0.15,
            score=round(validity, 2),
            detail=f"Average per numeric column: 3 points lost per 1% of IQR outliers "
            f"({outliers:,} values flagged in total).",
        ),
        QualityComponent(
            name="Open findings",
            weight=0.15,
            score=round(findings, 2),
            detail=f"100 − (10 × {high} high + 4 × {medium} medium + 1 × {low} low) severity findings.",
        ),
    ]
    score = sum(c.weight * c.score for c in components)
    grade = "A" if score >= 95 else "B" if score >= 88 else "C" if score >= 78 else "D" if score >= 65 else "E"
    return QualityScore(
        score=round(score, 1),
        grade=grade,
        formula="score = 0.30·completeness + 0.15·row uniqueness + 0.10·type usability + "
        "0.15·value consistency + 0.15·numeric validity + 0.15·open findings",
        components=components,
    )


def role_counts(profiles: list[ColumnProfile]) -> dict[str, int]:
    counts = {
        "numeric": 0,
        "categorical": 0,
        "datetime": 0,
        "boolean": 0,
        "identifier": 0,
        "free_text": 0,
        "geography": 0,
        "sensitive": 0,
        "unusable": 0,
    }
    for p in profiles:
        if p.analytical_role in (AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage):
            counts["numeric"] += 1
        elif p.analytical_role == AnalyticalRole.categorical_dimension:
            counts["categorical"] += 1
        elif p.analytical_role == AnalyticalRole.datetime_dimension:
            counts["datetime"] += 1
        elif p.analytical_role == AnalyticalRole.identifier:
            counts["identifier"] += 1
        elif p.analytical_role == AnalyticalRole.free_text:
            counts["free_text"] += 1
        elif p.analytical_role == AnalyticalRole.geography:
            counts["geography"] += 1
        elif p.analytical_role == AnalyticalRole.sensitive:
            counts["sensitive"] += 1
        else:
            counts["unusable"] += 1
        if p.physical_type == PhysicalType.boolean:
            counts["boolean"] += 1
    return counts


def build_overview(
    dataset_id: str,
    df: pd.DataFrame,
    profiles: list[ColumnProfile],
    *,
    filename: str,
    file_size_bytes: int,
    total_rows: int,
    analyzed_rows: int,
    sampled: bool,
    sample_method: str | None,
    business_context: str,
    notes: list[str],
    anomalies: list | None = None,
    created_at: datetime | None = None,
) -> dict:
    duplicate_rows = int(df.duplicated().sum())
    dup_pct = round(100 * duplicate_rows / analyzed_rows, 3) if analyzed_rows else 0.0
    missing_cells = int(df.isna().sum().sum())
    cells = analyzed_rows * max(1, len(profiles))
    miss_pct = round(100 * missing_cells / cells, 3) if cells else 0.0
    cat_cols = [p for p in profiles if p.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)]

    return dict(
        dataset_id=dataset_id,
        filename=filename,
        created_at=created_at or datetime.now(timezone.utc),
        row_count=total_rows,
        column_count=len(profiles),
        analyzed_row_count=analyzed_rows,
        sampled=sampled,
        sample_method=sample_method,
        file_size_bytes=file_size_bytes,
        memory_bytes=int(df.memory_usage(deep=True).sum()),
        duplicate_row_count=duplicate_rows,
        duplicate_row_percent=dup_pct,
        missing_cell_count=missing_cells,
        missing_cell_percent=miss_pct,
        role_counts=role_counts(profiles),
        categorical_column_count=len(cat_cols),
        distinct_category_values=int(sum(p.distinct_count for p in cat_cols)),
        quality=quality_score(df, profiles, dup_pct, miss_pct, anomalies),
        business_context=business_context,
        ingestion_notes=notes,
    )
