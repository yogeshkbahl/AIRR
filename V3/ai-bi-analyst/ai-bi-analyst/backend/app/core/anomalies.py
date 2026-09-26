"""Anomaly detection. Every finding states its method, scope and next action.

Findings that depend on business meaning (a negative price, a future order date)
are labelled as hypotheses, not facts.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..config import settings
from ..schemas import AnalyticalRole, Anomaly, EvidenceKind, PhysicalType, Severity
from .semantics import ColumnSemantics

NEGATIVE_SUSPECT_RE = re.compile(
    r"(qty|quantity|count|units|age|price|amount|revenue|sales|weight|duration|balance_due)"
)
LOCATOR_NAME_RE = re.compile(r"(zip|postal|postcode|phone|mobile|pincode|latitude|longitude)")


def _sev(percent: float, *, high: float = 20.0, medium: float = 5.0, low: float = 0.5) -> Severity:
    if percent >= high:
        return Severity.high
    if percent >= medium:
        return Severity.medium
    if percent >= low:
        return Severity.low
    return Severity.info


def detect_anomalies(df: pd.DataFrame, semantics: list[ColumnSemantics]) -> list[Anomaly]:
    rows = len(df)
    found: list[Anomaly] = []
    add = found.append
    seq = {"n": 0}

    def aid(kind: str, col: str = "") -> str:
        seq["n"] += 1
        return f"{kind}-{seq['n']}" + (f"-{re.sub(r'[^a-z0-9]+', '_', col.lower())}" if col else "")

    def pct(n: int) -> float:
        return round(100 * n / rows, 3) if rows else 0.0

    # ---------------- dataset level ----------------
    dupes = int(df.duplicated().sum())
    if dupes:
        add(
            Anomaly(
                id=aid("duplicate-rows"),
                kind="duplicate_rows",
                title=f"{dupes:,} fully duplicated rows",
                severity=_sev(pct(dupes)),
                columns=[],
                affected_rows=dupes,
                affected_percent=pct(dupes),
                method="pandas.DataFrame.duplicated() across all columns",
                explanation="These rows repeat every value of an earlier row, which inflates every sum and count.",
                recommended_action=(
                    "Confirm whether the source is expected to emit repeats. If not, de-duplicate before reporting."
                ),
                row_filter={"type": "duplicate_rows"},
            )
        )

    key_candidates = [
        s
        for s in semantics
        if s.analytical_role == AnalyticalRole.identifier and not LOCATOR_NAME_RE.search(s.name.lower())
    ]
    for s in key_candidates:
        dup_keys = int(df[s.name].duplicated(keep=False).sum())
        if dup_keys and s.uniqueness_ratio > 0.5:
            add(
                Anomaly(
                    id=aid("duplicate-keys", s.name),
                    kind="duplicate_business_key",
                    title=f"'{s.label}' repeats values although it looks like a key",
                    severity=_sev(pct(dup_keys), high=10, medium=2, low=0.1),
                    columns=[s.name],
                    affected_rows=dup_keys,
                    affected_percent=pct(dup_keys),
                    method=f"duplicated(keep=False) on {s.name} (uniqueness ratio {s.uniqueness_ratio:.2%})",
                    explanation="A repeated key changes the grain of the table and can silently double-count measures.",
                    recommended_action=(
                        "Decide the intended grain. If repeats are legitimate, define the true composite key."
                    ),
                    evidence_kind=EvidenceKind.inference,
                    row_filter={"type": "duplicate_values", "column": s.name},
                )
            )

    # ---------------- column level ----------------
    for s in semantics:
        col = df[s.name]

        if s.null_count:
            add(
                Anomaly(
                    id=aid("missing", s.name),
                    kind="missing_values",
                    title=f"'{s.label}' is missing {s.null_count:,} values",
                    severity=_sev(pct(s.null_count)),
                    columns=[s.name],
                    affected_rows=s.null_count,
                    affected_percent=pct(s.null_count),
                    method="isna() count over analysed rows",
                    explanation="Nulls change averages and drop rows from joins and filters.",
                    recommended_action=(
                        "Agree a treatment: exclude, impute, or display as an explicit 'Unknown' member."
                    ),
                    row_filter={"type": "is_null", "column": s.name},
                )
            )

        if s.non_null_count == 0:
            add(
                Anomaly(
                    id=aid("empty", s.name),
                    kind="empty_column",
                    title=f"'{s.label}' has no values at all",
                    severity=Severity.medium,
                    columns=[s.name],
                    affected_rows=rows,
                    affected_percent=100.0,
                    method="non-null count = 0",
                    explanation="The column carries no information for analysis.",
                    recommended_action="Remove it from the report or fix the extract that populates it.",
                )
            )
            continue

        if s.distinct_count == 1:
            only = str(col.dropna().iloc[0])[:60]
            add(
                Anomaly(
                    id=aid("constant", s.name),
                    kind="constant_column",
                    title=f"'{s.label}' is constant ({only})",
                    severity=Severity.low,
                    columns=[s.name],
                    affected_rows=s.non_null_count,
                    affected_percent=pct(s.non_null_count),
                    method="distinct count = 1",
                    explanation="A single value cannot differentiate anything, so it adds no analytical value.",
                    recommended_action="Use it as a report header or filter label rather than a dimension.",
                )
            )
        elif s.distinct_count > 1 and s.non_null_count > 20:
            top_share = float(col.value_counts(normalize=True).iloc[0])
            if top_share >= settings.near_constant_threshold:
                add(
                    Anomaly(
                        id=aid("near-constant", s.name),
                        kind="near_constant_column",
                        title=f"'{s.label}' is {top_share:.1%} one value",
                        severity=Severity.low,
                        columns=[s.name],
                        affected_rows=int(top_share * s.non_null_count),
                        affected_percent=round(100 * top_share, 2),
                        method=f"dominant value share ≥ {settings.near_constant_threshold:.0%}",
                        explanation="Charts split by this column will look almost empty outside the dominant value.",
                        recommended_action="Group the minority values or drop the column from comparisons.",
                    )
                )

        if s.physical_type in (PhysicalType.integer, PhysicalType.float) and s.analytical_role in (
            AnalyticalRole.measure,
            AnalyticalRole.currency,
            AnalyticalRole.percentage,
        ):
            found.extend(_numeric_anomalies(col, s, rows, aid, pct))

        if s.physical_type == PhysicalType.string:
            found.extend(_string_anomalies(col, s, rows, aid, pct))

        if s.physical_type in (PhysicalType.date, PhysicalType.datetime_):
            parsed = s.parsed_datetime if s.parsed_datetime is not None else pd.to_datetime(col, errors="coerce")
            found.extend(_date_anomalies(parsed, col, s, rows, aid, pct))

        text_dimension = (
            s.physical_type == PhysicalType.string and s.analytical_role == AnalyticalRole.categorical_dimension
        )
        if text_dimension and s.uniqueness_ratio > settings.high_cardinality_ratio and s.distinct_count > 100:
            add(
                Anomaly(
                    id=aid("cardinality", s.name),
                    kind="high_cardinality",
                    title=f"'{s.label}' has {s.distinct_count:,} distinct values",
                    severity=Severity.medium,
                    columns=[s.name],
                    affected_rows=s.non_null_count,
                    affected_percent=pct(s.non_null_count),
                    method=f"uniqueness ratio {s.uniqueness_ratio:.2%} above {settings.high_cardinality_ratio:.0%}",
                    explanation="Too many members to chart directly; it is either a key or needs grouping.",
                    recommended_action="Reclassify as an identifier, or roll values up into a coarser attribute.",
                    evidence_kind=EvidenceKind.inference,
                )
            )

    found.extend(_referential_anomalies(df, semantics, rows, aid, pct))
    order = {Severity.high: 0, Severity.medium: 1, Severity.low: 2, Severity.info: 3}
    found.sort(key=lambda a: (order[a.severity], -a.affected_percent))
    return found


def _numeric_anomalies(col, s, rows, aid, pct) -> list[Anomaly]:
    out: list[Anomaly] = []
    clean = pd.to_numeric(col, errors="coerce").dropna()
    if clean.empty:
        return out

    q1, q3 = float(clean.quantile(0.25)), float(clean.quantile(0.75))
    iqr = q3 - q1
    if iqr > 0:
        lo, hi = q1 - settings.iqr_multiplier * iqr, q3 + settings.iqr_multiplier * iqr
        n = int(((clean < lo) | (clean > hi)).sum())
        if n:
            out.append(
                Anomaly(
                    id=aid("outlier-iqr", s.name),
                    kind="numeric_outliers",
                    title=f"'{s.label}' has {n:,} values outside the IQR fence",
                    severity=_sev(pct(n), high=10, medium=3, low=0.2),
                    columns=[s.name],
                    affected_rows=n,
                    affected_percent=pct(n),
                    method=f"Tukey fence: outside [{lo:,.2f}, {hi:,.2f}] using {settings.iqr_multiplier}×IQR",
                    explanation="Extreme values pull means and totals and can dominate a chart's axis.",
                    recommended_action="Check the largest values against source records before excluding anything.",
                    evidence_kind=EvidenceKind.inference,
                    row_filter={"type": "outside_range", "column": s.name, "min": lo, "max": hi},
                )
            )

    median = float(clean.median())
    mad = float((clean - median).abs().median())
    if mad > 0:
        rz = (0.6745 * (clean - median) / mad).abs()
        n = int((rz > settings.robust_z_threshold).sum())
        if n:
            out.append(
                Anomaly(
                    id=aid("outlier-z", s.name),
                    kind="numeric_outliers_robust",
                    title=f"'{s.label}' has {n:,} values with robust z > {settings.robust_z_threshold}",
                    severity=_sev(pct(n), high=10, medium=3, low=0.2),
                    columns=[s.name],
                    affected_rows=n,
                    affected_percent=pct(n),
                    method=f"median absolute deviation, threshold {settings.robust_z_threshold}",
                    explanation="A skew-resistant check that agrees with the IQR test only when outliers are genuine.",
                    recommended_action=(
                        "Compare with the IQR finding; values flagged by both deserve investigation first."
                    ),
                    evidence_kind=EvidenceKind.inference,
                )
            )

    neg = int((clean < 0).sum())
    if neg and NEGATIVE_SUSPECT_RE.search(s.name.lower()):
        out.append(
            Anomaly(
                id=aid("negative", s.name),
                kind="suspicious_values",
                title=f"'{s.label}' contains {neg:,} negative values",
                severity=_sev(pct(neg), high=10, medium=2, low=0.1),
                columns=[s.name],
                affected_rows=neg,
                affected_percent=pct(neg),
                method="sign check on a column whose name implies a non-negative quantity",
                explanation="Negatives may be valid returns or credits, or may be a sign-convention error upstream.",
                recommended_action=(
                    "Confirm the business rule. If returns are expected, split them into their own measure."
                ),
                evidence_kind=EvidenceKind.assumption,
                row_filter={"type": "less_than", "column": s.name, "value": 0},
            )
        )

    if s.analytical_role == AnalyticalRole.percentage:
        bad = (
            int(((clean < 0) | (clean > 100)).sum())
            if float(clean.max()) > 1.5
            else int(((clean < 0) | (clean > 1)).sum())
        )
        if bad:
            out.append(
                Anomaly(
                    id=aid("pct-range", s.name),
                    kind="suspicious_values",
                    title=f"'{s.label}' has {bad:,} values outside the expected percentage range",
                    severity=_sev(pct(bad), high=10, medium=2, low=0.1),
                    columns=[s.name],
                    affected_rows=bad,
                    affected_percent=pct(bad),
                    method="range check against 0-1 or 0-100 depending on the observed maximum",
                    explanation="Mixed percentage scales are a common cause of wrong KPI values.",
                    recommended_action="Standardise the scale in the source or the semantic layer.",
                    evidence_kind=EvidenceKind.assumption,
                )
            )
    return out


def _string_anomalies(col, s, rows, aid, pct) -> list[Anomaly]:
    out: list[Anomaly] = []
    text = col.dropna().astype(str)
    if text.empty:
        return out

    blanks = int((text.str.strip() == "").sum())
    if blanks:
        out.append(
            Anomaly(
                id=aid("blank", s.name),
                kind="blank_strings",
                title=f"'{s.label}' has {blanks:,} blank strings stored as data",
                severity=_sev(pct(blanks)),
                columns=[s.name],
                affected_rows=blanks,
                affected_percent=pct(blanks),
                method="empty value after trimming whitespace",
                explanation="Blank strings are not counted as nulls, so quality checks and filters miss them.",
                recommended_action="Convert them to real nulls or to an explicit 'Unknown' member.",
                row_filter={"type": "blank_string", "column": s.name},
            )
        )

    ws = int((text != text.str.strip()).sum())
    if ws:
        out.append(
            Anomaly(
                id=aid("whitespace", s.name),
                kind="whitespace",
                title=f"'{s.label}' has {ws:,} values with leading or trailing spaces",
                severity=Severity.low if pct(ws) < 5 else Severity.medium,
                columns=[s.name],
                affected_rows=ws,
                affected_percent=pct(ws),
                method="comparison of raw values with trimmed values",
                explanation="'North ' and 'North' group separately, which splits totals across near-identical members.",
                recommended_action="Trim on load and add a trim rule to the semantic layer.",
                row_filter={"type": "whitespace", "column": s.name},
            )
        )

    folded = text.str.strip().str.casefold()
    pairs = pd.DataFrame({"raw": text.str.strip(), "folded": folded}).drop_duplicates()
    variants = pairs.groupby("folded")["raw"].nunique()
    groups = int((variants > 1).sum())
    if groups:
        examples = ", ".join(sorted(pairs[pairs["folded"].isin(variants[variants > 1].index)]["raw"].unique())[:4])
        out.append(
            Anomaly(
                id=aid("casing", s.name),
                kind="inconsistent_casing",
                title=f"'{s.label}' has {groups} values that differ only by case or spacing",
                severity=Severity.medium if groups > 3 else Severity.low,
                columns=[s.name],
                affected_rows=int(text.str.strip().str.casefold().isin(variants[variants > 1].index).sum()),
                affected_percent=pct(int(text.str.strip().str.casefold().isin(variants[variants > 1].index).sum())),
                method="case-folded grouping of trimmed values",
                explanation=f"Examples: {examples}. These split one real member into several chart bars.",
                recommended_action="Standardise casing on load and keep a mapping table for accepted labels.",
                row_filter={"type": "casing_variant", "column": s.name},
            )
        )

    if s.analytical_role == AnalyticalRole.categorical_dimension and s.distinct_count > 5:
        counts = text.value_counts(normalize=True)
        rare = counts[counts < settings.rare_category_threshold]
        if len(rare) > 2:
            out.append(
                Anomaly(
                    id=aid("rare", s.name),
                    kind="rare_categories",
                    title=(
                        f"'{s.label}' has {len(rare)} categories below {settings.rare_category_threshold:.0%} of rows"
                    ),
                    severity=Severity.low,
                    columns=[s.name],
                    affected_rows=int(rare.sum() * len(text)),
                    affected_percent=round(100 * float(rare.sum()), 3),
                    method=f"relative frequency below {settings.rare_category_threshold:.0%}",
                    explanation="A long tail of tiny members makes bar and pie charts unreadable.",
                    recommended_action=(
                        "Group the tail into 'Other' for reporting, keeping detail available on drill-down."
                    ),
                )
            )

    numeric_like = pd.to_numeric(text, errors="coerce").notna().mean()
    if 0.6 <= numeric_like < 1.0:
        bad = int((pd.to_numeric(text, errors="coerce").isna()).sum())
        out.append(
            Anomaly(
                id=aid("mixed-type", s.name),
                kind="mixed_types",
                title=f"'{s.label}' is mostly numeric but {bad:,} values are not",
                severity=Severity.medium,
                columns=[s.name],
                affected_rows=bad,
                affected_percent=pct(bad),
                method=f"{numeric_like:.1%} of values convert to a number",
                explanation="Mixed storage blocks aggregation and usually hides placeholders such as 'N/A' or 'TBD'.",
                recommended_action="Clean the placeholders, then cast the column to a numeric type.",
                row_filter={"type": "not_numeric", "column": s.name},
            )
        )
    return out


def _date_anomalies(parsed, original, s, rows, aid, pct) -> list[Anomaly]:
    out: list[Anomaly] = []
    invalid = int((original.notna() & parsed.isna()).sum())
    if invalid:
        out.append(
            Anomaly(
                id=aid("invalid-date", s.name),
                kind="invalid_dates",
                title=f"'{s.label}' has {invalid:,} unparseable dates",
                severity=_sev(pct(invalid), high=5, medium=1, low=0.1),
                columns=[s.name],
                affected_rows=invalid,
                affected_percent=pct(invalid),
                method="pandas.to_datetime with coercion",
                explanation="Unparseable dates silently disappear from every time-based report.",
                recommended_action="Fix the offending formats at source, or agree an explicit parse rule.",
                row_filter={"type": "invalid_date", "column": s.name},
            )
        )

    clean = parsed.dropna()
    if clean.empty:
        return out

    future = int((clean > pd.Timestamp.now()).sum())
    if future:
        out.append(
            Anomaly(
                id=aid("future-date", s.name),
                kind="future_dates",
                title=f"'{s.label}' has {future:,} dates in the future",
                severity=_sev(pct(future), high=5, medium=1, low=0.1),
                columns=[s.name],
                affected_rows=future,
                affected_percent=pct(future),
                method="comparison against the current timestamp",
                explanation="Future dates are valid for forecasts and contract end dates, and wrong for transactions.",
                recommended_action="Confirm the column's meaning before excluding anything.",
                evidence_kind=EvidenceKind.assumption,
                row_filter={"type": "future_date", "column": s.name},
            )
        )

    daily = clean.dt.floor("D").drop_duplicates().sort_values()
    if len(daily) > 5:
        gaps = daily.diff().dropna().dt.days
        typical = float(gaps.median())
        big = gaps[gaps > max(3 * typical, typical + 7)]
        if len(big):
            out.append(
                Anomaly(
                    id=aid("date-gap", s.name),
                    kind="date_gaps",
                    title=f"'{s.label}' has {len(big)} gaps in its calendar coverage",
                    severity=Severity.medium if len(big) > 3 else Severity.low,
                    columns=[s.name],
                    affected_rows=int(big.sum()),
                    affected_percent=0.0,
                    method=f"gaps longer than 3× the median interval of {typical:.0f} days",
                    explanation=f"The largest gap is {int(big.max())} days, which makes trend lines misleading.",
                    recommended_action=(
                        "Join to a full date dimension so missing periods show as zero rather than being skipped."
                    ),
                    evidence_kind=EvidenceKind.inference,
                )
            )

    if len(clean) > 50:
        by_hour = clean.dt.hour.value_counts()
        if len(by_hour) > 1 and float((clean.dt.hour == 0).mean()) > 0.5 and float((clean.dt.hour == 0).mean()) < 0.99:
            out.append(
                Anomaly(
                    id=aid("granularity", s.name),
                    kind="inconsistent_granularity",
                    title=f"'{s.label}' mixes date-only and timestamp values",
                    severity=Severity.low,
                    columns=[s.name],
                    affected_rows=int((clean.dt.hour == 0).sum()),
                    affected_percent=round(100 * float((clean.dt.hour == 0).mean()), 2),
                    method="share of values at exactly midnight",
                    explanation="Mixed grain breaks hourly analysis and can bias 'first event of day' logic.",
                    recommended_action="Store date and time separately, or truncate consistently to the day.",
                )
            )
    return out


def _referential_anomalies(df, semantics, rows, aid, pct) -> list[Anomaly]:
    """Look for key-like pairs where one column implies another but disagrees."""
    out: list[Anomaly] = []
    ids = [
        s for s in semantics if s.analytical_role in (AnalyticalRole.identifier, AnalyticalRole.categorical_dimension)
    ]
    attrs = [
        s for s in semantics if s.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)
    ]
    checked = 0
    for key in ids:
        # A column that is near-unique per row is a row key, not a dimension key,
        # so an attribute "conflict" there is meaningless.
        if key.distinct_count < 2 or key.distinct_count > 5000 or key.uniqueness_ratio > 0.5:
            continue
        if LOCATOR_NAME_RE.search(key.name.lower()):
            continue
        for attr in attrs:
            if attr.name == key.name or checked >= 40:
                continue
            checked += 1
            sub = df[[key.name, attr.name]].dropna()
            if len(sub) < 20:
                continue
            per_key = sub.groupby(key.name)[attr.name].nunique()
            conflicting = per_key[per_key > 1]
            if len(conflicting) and len(conflicting) / max(1, len(per_key)) < 0.5:
                affected = int(sub[sub[key.name].isin(conflicting.index)].shape[0])
                out.append(
                    Anomaly(
                        id=aid("referential", f"{key.name}_{attr.name}"),
                        kind="referential_inconsistency",
                        title=f"'{key.label}' maps to more than one '{attr.label}'",
                        severity=Severity.medium,
                        columns=[key.name, attr.name],
                        affected_rows=affected,
                        affected_percent=pct(affected),
                        method=f"group by {key.name} and count distinct {attr.name}",
                        explanation=(
                            f"{len(conflicting)} of {len(per_key)} key values carry conflicting "
                            "attribute values, which usually means a slowly-changing dimension "
                            "flattened into the fact table."
                        ),
                        recommended_action=(
                            "Decide whether history matters. If it does, model it as a dimension with effective dates."
                        ),
                        evidence_kind=EvidenceKind.inference,
                    )
                )
                if len(out) >= 3:
                    return out
    return out


def affected_rows_preview(df: pd.DataFrame, row_filter: dict, sensitive_cols: set[str], limit: int) -> pd.DataFrame:
    """Return matching rows with sensitive columns removed."""
    ftype = row_filter.get("type")
    col = row_filter.get("column")
    if ftype == "duplicate_rows":
        mask = df.duplicated(keep=False)
    elif ftype == "duplicate_values" and col:
        mask = df[col].duplicated(keep=False)
    elif ftype == "is_null" and col:
        mask = df[col].isna()
    elif ftype == "outside_range" and col:
        v = pd.to_numeric(df[col], errors="coerce")
        mask = (v < row_filter["min"]) | (v > row_filter["max"])
    elif ftype == "less_than" and col:
        mask = pd.to_numeric(df[col], errors="coerce") < row_filter.get("value", 0)
    elif ftype == "blank_string" and col:
        mask = df[col].astype(str).str.strip().eq("") & df[col].notna()
    elif ftype == "whitespace" and col:
        raw = df[col].astype(str)
        mask = df[col].notna() & raw.ne(raw.str.strip())
    elif ftype == "casing_variant" and col:
        text = df[col].dropna().astype(str).str.strip()
        variants = pd.DataFrame({"raw": text, "folded": text.str.casefold()}).drop_duplicates()
        multi = variants.groupby("folded")["raw"].nunique()
        keys = set(multi[multi > 1].index)
        mask = df[col].astype(str).str.strip().str.casefold().isin(keys) & df[col].notna()
    elif ftype == "not_numeric" and col:
        mask = df[col].notna() & pd.to_numeric(df[col], errors="coerce").isna()
    elif ftype == "invalid_date" and col:
        mask = df[col].notna() & pd.to_datetime(df[col], errors="coerce").isna()
    elif ftype == "future_date" and col:
        mask = pd.to_datetime(df[col], errors="coerce") > pd.Timestamp.now()
    else:
        mask = pd.Series(np.zeros(len(df), dtype=bool), index=df.index)
    keep = [c for c in df.columns if c not in sensitive_cols]
    return df.loc[mask, keep].head(limit)
