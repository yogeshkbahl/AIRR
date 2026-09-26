"""Type-aware relationship engine.

The method is chosen from the pair of analytical roles, never applied blindly:

    numeric  x numeric      Pearson (+ Spearman)
    category x category     Cramer's V (+ chi-square)
    category x numeric      correlation ratio (eta) (+ one-way ANOVA)
    date     x numeric      Spearman against time (monotonic trend)
    boolean  x numeric      point-biserial
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..config import settings
from ..schemas import (
    Aggregation,
    AnalyticalRole,
    ChartEncoding,
    ChartSpec,
    RelationshipDetail,
    RelationshipMatrix,
    RelationshipResult,
    SegmentBreakdown,
)
from .semantics import ColumnSemantics, safe_float

NUMERIC_ROLES = {AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage}
CATEGORY_ROLES = {AnalyticalRole.categorical_dimension, AnalyticalRole.geography}


def _kind(sem: ColumnSemantics) -> str:
    if sem.analytical_role in NUMERIC_ROLES:
        return "numeric"
    if sem.analytical_role == AnalyticalRole.datetime_dimension:
        return "date"
    if sem.analytical_role in CATEGORY_ROLES:
        return "boolean" if sem.distinct_count == 2 else "categorical"
    return "unsupported"


def pair_kind(a: ColumnSemantics, b: ColumnSemantics) -> str:
    ka, kb = _kind(a), _kind(b)
    if "unsupported" in (ka, kb):
        return "unsupported"
    pair = tuple(sorted([ka, kb]))
    return "-".join(pair)


def cramers_v(x: pd.Series, y: pd.Series) -> tuple[float | None, float | None, int]:
    table = pd.crosstab(x, y)
    if table.shape[0] < 2 or table.shape[1] < 2:
        return None, None, int(table.to_numpy().sum())
    chi2, p, _, _ = stats.chi2_contingency(table)
    n = int(table.to_numpy().sum())
    denom = n * (min(table.shape) - 1)
    if denom <= 0:
        return None, p, n
    # bias-corrected Cramer's V
    phi2 = chi2 / n
    r, k = table.shape
    phi2corr = max(0.0, phi2 - ((k - 1) * (r - 1)) / max(1, (n - 1)))
    rcorr = r - ((r - 1) ** 2) / max(1, (n - 1))
    kcorr = k - ((k - 1) ** 2) / max(1, (n - 1))
    v = np.sqrt(phi2corr / max(1e-12, min(kcorr - 1, rcorr - 1)))
    return float(min(1.0, v)), float(p), n


def correlation_ratio(categories: pd.Series, values: pd.Series) -> tuple[float | None, float | None, int]:
    """eta: share of variance in `values` explained by group membership."""
    df = pd.DataFrame({"c": categories.astype(str), "v": pd.to_numeric(values, errors="coerce")}).dropna()
    if df["c"].nunique() < 2 or len(df) < 3:
        return None, None, len(df)
    groups = [g["v"].to_numpy() for _, g in df.groupby("c") if len(g) > 0]
    overall = df["v"].mean()
    ss_between = sum(len(g) * (g.mean() - overall) ** 2 for g in groups)
    ss_total = float(((df["v"] - overall) ** 2).sum())
    eta = float(np.sqrt(ss_between / ss_total)) if ss_total > 0 else None
    p = None
    usable = [g for g in groups if len(g) > 1]
    if len(usable) >= 2:
        try:
            p = float(stats.f_oneway(*usable).pvalue)
        except Exception:
            p = None
    return eta, p, len(df)


def _effect_label(method: str, stat: float | None) -> str | None:
    if stat is None:
        return None
    a = abs(stat)
    if method.startswith("cramer") or method.startswith("correlation_ratio"):
        return "negligible" if a < 0.1 else "small" if a < 0.3 else "moderate" if a < 0.5 else "strong"
    return "negligible" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong" if a < 0.7 else "very strong"


def analyse_pair(df: pd.DataFrame, a: ColumnSemantics, b: ColumnSemantics) -> RelationshipResult:
    kind = pair_kind(a, b)
    sub = df[[a.name, b.name]]
    before = len(sub)
    sub = sub.dropna()
    dropped = before - len(sub)
    warnings: list[str] = []
    result = RelationshipResult(
        column_x=a.name,
        column_y=b.name,
        pair_kind=kind,
        method="none",
        sample_size=len(sub),
        missing_dropped=dropped,
    )

    if kind == "unsupported":
        result.reliable = False
        result.warnings = ["At least one column has a role that cannot be correlated (identifier or free text)."]
        result.interpretation = "No statistic is appropriate for this pair."
        return result

    if len(sub) < settings.min_pair_sample:
        result.reliable = False
        result.warnings = [f"Only {len(sub)} complete pairs; at least {settings.min_pair_sample} are needed."]
        result.interpretation = "Sample too small to report an association."
        return result

    if a.distinct_count < 2 or b.distinct_count < 2:
        result.reliable = False
        result.warnings = ["One column is constant, so no association can exist."]
        return result

    x, y = sub[a.name], sub[b.name]

    if kind == "numeric-numeric":
        result.method = "pearson"
        r, p = stats.pearsonr(pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce"))
        rs, _ = stats.spearmanr(x, y)
        result.statistic, result.p_value = safe_float(r), safe_float(p)
        result.secondary_method, result.secondary_statistic = "spearman", safe_float(rs)
        if result.statistic is not None and result.secondary_statistic is not None:
            if abs(result.statistic - result.secondary_statistic) > 0.2:
                warnings.append("Pearson and Spearman disagree, which points to outliers or a non-linear shape.")

    elif kind == "categorical-categorical" or kind in ("boolean-categorical", "boolean-boolean"):
        result.method = "cramers_v"
        v, p, n = cramers_v(x.astype(str), y.astype(str))
        result.statistic, result.p_value, result.sample_size = v, safe_float(p) if p else None, n
        result.secondary_method = "chi_square_p"
        if max(a.distinct_count, b.distinct_count) > 50:
            warnings.append("High cardinality inflates chi-square based measures; group the members first.")

    elif kind in ("categorical-numeric", "boolean-numeric"):
        cat, num = (a, b) if _kind(a) in ("categorical", "boolean") else (b, a)
        if _kind(cat) == "boolean" and _kind(num) == "numeric":
            result.method = "point_biserial"
            codes = pd.factorize(sub[cat.name].astype(str))[0]
            r, p = stats.pointbiserialr(codes, pd.to_numeric(sub[num.name], errors="coerce"))
            result.statistic, result.p_value = safe_float(r), safe_float(p)
        else:
            result.method = "correlation_ratio_eta"
            eta, p, n = correlation_ratio(sub[cat.name], sub[num.name])
            result.statistic, result.p_value, result.sample_size = eta, safe_float(p) if p else None, n
            result.secondary_method = "anova_p"
        if cat.distinct_count > 30:
            warnings.append(f"'{cat.label}' has {cat.distinct_count} groups; group comparisons will be noisy.")

    elif kind in ("date-numeric",):
        date_col, num_col = (a, b) if _kind(a) == "date" else (b, a)
        dt = pd.to_datetime(sub[date_col.name], errors="coerce")
        num = pd.to_numeric(sub[num_col.name], errors="coerce")
        ok = dt.notna() & num.notna()
        result.method = "spearman_vs_time"
        rs, p = stats.spearmanr(dt[ok].astype("int64"), num[ok])
        result.statistic, result.p_value = safe_float(rs), safe_float(p)
        result.sample_size = int(ok.sum())
        monthly = pd.DataFrame({"t": dt[ok], "v": num[ok]}).set_index("t")["v"].resample("MS").mean().dropna()
        if len(monthly) >= 4:
            slope = float(np.polyfit(range(len(monthly)), monthly.to_numpy(), 1)[0])
            result.secondary_method = "monthly_ols_slope"
            result.secondary_statistic = safe_float(slope)

    elif kind in ("date-categorical", "date-date", "boolean-date"):
        result.method = "not_applicable"
        result.reliable = False
        warnings.append("Associations between two dimensions of this kind are better explored as a crosstab.")

    else:
        result.method = "not_applicable"
        result.reliable = False

    if result.statistic is None and result.method not in ("not_applicable",):
        result.reliable = False
        warnings.append("The statistic could not be computed for this pair.")

    if dropped / max(1, before) > 0.5:
        warnings.append(f"{dropped:,} of {before:,} rows were dropped for missing values; treat the result as indicative.")

    result.warnings = warnings
    result.effect_size_label = _effect_label(result.method, result.statistic)
    result.interpretation = _interpret(result, a, b)
    return result


def _interpret(r: RelationshipResult, a: ColumnSemantics, b: ColumnSemantics) -> str:
    if r.statistic is None:
        return "No association statistic is available for this pair."
    strength = r.effect_size_label or "unclear"
    direction = ""
    if r.method in ("pearson", "spearman_vs_time", "point_biserial"):
        direction = " positive" if r.statistic > 0 else " negative" if r.statistic < 0 else ""
    sig = ""
    if r.p_value is not None:
        sig = f" p = {r.p_value:.4g}" if r.p_value >= 0.0001 else " p < 0.0001"
    base = (
        f"{r.method.replace('_', ' ')} = {r.statistic:.3f} on {r.sample_size:,} complete pairs: "
        f"a{direction} {strength} association between '{a.label}' and '{b.label}'.{sig}"
    )
    return base + " Association is not causation; a driver analysis or an experiment is needed for that."


def build_matrix(
    df: pd.DataFrame, semantics: list[ColumnSemantics], columns: list[str] | None = None
) -> RelationshipMatrix:
    by_name = {s.name: s for s in semantics}
    usable = [
        s
        for s in semantics
        if _kind(s) != "unsupported" and (columns is None or s.name in columns)
    ][: settings.max_association_columns]
    names = [s.name for s in usable]
    n = len(names)
    values: list[list[float | None]] = [[None] * n for _ in range(n)]
    methods: list[list[str | None]] = [[None] * n for _ in range(n)]
    pairs: list[RelationshipResult] = []
    suppressed = 0

    for i in range(n):
        values[i][i] = 1.0
        methods[i][i] = "identity"
        for j in range(i + 1, n):
            res = analyse_pair(df, by_name[names[i]], by_name[names[j]])
            pairs.append(res)
            if res.reliable and res.statistic is not None:
                values[i][j] = values[j][i] = round(res.statistic, 4)
                methods[i][j] = methods[j][i] = res.method
            else:
                suppressed += 1
    pairs.sort(key=lambda r: abs(r.statistic or 0), reverse=True)
    return RelationshipMatrix(
        columns=names,
        labels=[by_name[c].label for c in names],
        values=values,
        methods=methods,
        pairs=pairs,
        suppressed_pairs=suppressed,
    )


def segment_check(
    df: pd.DataFrame, a: ColumnSemantics, b: ColumnSemantics, segment: ColumnSemantics
) -> SegmentBreakdown | None:
    """Recompute the pair inside each segment and flag a sign reversal (Simpson's paradox)."""
    overall = analyse_pair(df, a, b)
    if overall.statistic is None or overall.method not in ("pearson", "spearman_vs_time", "point_biserial"):
        return None
    segments, stats_out, sizes = [], [], []
    top = df[segment.name].value_counts().head(8).index
    for value in top:
        sub = df[df[segment.name] == value]
        if len(sub) < settings.min_pair_sample:
            continue
        res = analyse_pair(sub, a, b)
        segments.append(str(value)[:60])
        stats_out.append(res.statistic)
        sizes.append(res.sample_size)
    if len(segments) < 2:
        return None
    signs = {np.sign(s) for s in stats_out if s is not None and abs(s) > 0.1}
    reversed_sign = bool(signs) and np.sign(overall.statistic) not in signs and len(signs) == 1
    return SegmentBreakdown(
        segment_column=segment.name,
        segments=segments,
        statistics=[safe_float(s) for s in stats_out],
        sample_sizes=sizes,
        simpsons_paradox_suspected=reversed_sign,
        note=(
            f"Within every segment of '{segment.label}' the association runs opposite to the overall figure "
            f"({overall.statistic:.3f}). Report the segmented view, not the pooled one."
            if reversed_sign
            else f"Segment-level results are consistent in direction with the overall figure ({overall.statistic:.3f})."
        ),
    )


def detail_chart(a: ColumnSemantics, b: ColumnSemantics, kind: str) -> ChartSpec:
    if kind == "numeric-numeric":
        return ChartSpec(
            chart_type="scatter",
            title=f"{a.label} vs {b.label}",
            encoding=ChartEncoding(x=a.name, y=b.name),
            aggregation=Aggregation.none,
            limit=5000,
            notes=["Points are raw rows; a trend line is fitted on the client."],
        )
    if kind == "date-numeric":
        date_col, num_col = (a, b) if a.analytical_role == AnalyticalRole.datetime_dimension else (b, a)
        return ChartSpec(
            chart_type="line",
            title=f"{num_col.label} over time",
            encoding=ChartEncoding(x=date_col.name, y=num_col.name),
            aggregation=Aggregation.sum,
            limit=1000,
        )
    if kind in ("categorical-numeric", "boolean-numeric"):
        cat, num = (a, b) if a.analytical_role in CATEGORY_ROLES else (b, a)
        return ChartSpec(
            chart_type="box",
            title=f"{num.label} distribution by {cat.label}",
            encoding=ChartEncoding(x=cat.name, y=num.name),
            aggregation=Aggregation.none,
            limit=20,
        )
    return ChartSpec(
        chart_type="heatmap",
        title=f"{a.label} vs {b.label} crosstab",
        encoding=ChartEncoding(x=a.name, y=b.name),
        aggregation=Aggregation.count,
        limit=400,
    )


def pair_detail(
    df: pd.DataFrame,
    semantics: list[ColumnSemantics],
    column_x: str,
    column_y: str,
    method_override: str | None = None,
    segment_by: str | None = None,
) -> RelationshipDetail:
    by_name = {s.name: s for s in semantics}
    a, b = by_name[column_x], by_name[column_y]
    result = analyse_pair(df, a, b)
    if method_override and method_override != result.method:
        result.warnings.append(
            f"Requested method '{method_override}' is not valid for a {result.pair_kind} pair; "
            f"'{result.method}' was used instead."
        )
    seg = None
    if segment_by and segment_by in by_name:
        seg = segment_check(df, a, b, by_name[segment_by])
    return RelationshipDetail(result=result, chart=detail_chart(a, b, result.pair_kind), segment=seg)
