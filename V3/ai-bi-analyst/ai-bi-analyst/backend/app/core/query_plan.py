"""Governed query execution.

No LLM-authored SQL or Python is ever executed. An `AnalysisPlan` is validated
against the real column list and then compiled to parameterised DuckDB SQL using
an allow-list of aggregate functions, with a row cap and a wall-clock timeout.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from ..config import settings
from ..schemas import (
    Aggregation,
    AnalysisPlan,
    AnalyticalRole,
    ChartSpec,
    MetricSpec,
    PlanResult,
    QueryFilter,
)
from .semantics import ColumnSemantics

AGG_SQL: dict[Aggregation, str] = {
    Aggregation.sum: "SUM({col})",
    Aggregation.avg: "AVG({col})",
    Aggregation.min: "MIN({col})",
    Aggregation.max: "MAX({col})",
    Aggregation.count: "COUNT({col})",
    Aggregation.count_distinct: "COUNT(DISTINCT {col})",
    Aggregation.median: "MEDIAN({col})",
}

GRAIN_SQL = {"day": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}

NUMERIC_ROLES = {AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage}


class PlanError(ValueError):
    """A plan that cannot be executed safely. The message is user-facing."""


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _alias(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")[:60] or "value"


def validate_plan(plan: AnalysisPlan, semantics: list[ColumnSemantics]) -> AnalysisPlan:
    by_name = {s.name: s for s in semantics}
    lower = {s.name.lower(): s.name for s in semantics}

    def resolve(name: str, what: str) -> str:
        if name in by_name:
            return name
        if name.lower() in lower:
            return lower[name.lower()]
        raise PlanError(f"{what} '{name}' is not a column in this dataset.")

    metrics: list[MetricSpec] = []
    for m in plan.metrics:
        col = resolve(m.column, "Metric column")
        sem = by_name[col]
        agg = m.aggregation
        if agg in (Aggregation.sum, Aggregation.avg, Aggregation.median) and sem.analytical_role not in NUMERIC_ROLES:
            if sem.analytical_role in (
                AnalyticalRole.identifier,
                AnalyticalRole.categorical_dimension,
                AnalyticalRole.geography,
            ):
                agg = Aggregation.count_distinct
            else:
                raise PlanError(f"'{sem.label}' cannot be aggregated with {m.aggregation.value}.")
        if sem.analytical_role == AnalyticalRole.percentage and agg == Aggregation.sum:
            agg = Aggregation.avg
        metrics.append(MetricSpec(column=col, aggregation=agg, label=m.label or f"{agg.value} of {sem.label}"))

    dims = []
    for d in plan.dimensions:
        col = resolve(d, "Dimension")
        if by_name[col].analytical_role == AnalyticalRole.free_text:
            continue
        dims.append(col)

    time_dim = resolve(plan.time_dimension, "Time dimension") if plan.time_dimension else None
    if time_dim and by_name[time_dim].analytical_role != AnalyticalRole.datetime_dimension:
        time_dim = None

    filters = []
    for f in plan.filters:
        col = resolve(f.column, "Filter column")
        filters.append(QueryFilter(column=col, op=f.op, value=f.value))

    sort_by = plan.sort_by
    if sort_by:
        known = {m.label for m in metrics} | {_alias(m.label or "") for m in metrics} | set(dims)
        if sort_by not in known and sort_by.lower() in lower:
            sort_by = lower[sort_by.lower()]
        elif sort_by not in known and metrics:
            sort_by = metrics[0].label

    if not metrics and not dims and not time_dim:
        raise PlanError("The plan has no metrics and no dimensions, so there is nothing to compute.")

    return plan.model_copy(
        update={
            "metrics": metrics,
            "dimensions": dims[:4],
            "time_dimension": time_dim,
            "filters": filters,
            "sort_by": sort_by,
            "limit": max(1, min(plan.limit or 100, settings.query_row_limit)),
        }
    )


def compile_plan(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    params: list[Any] = []
    select_parts: list[str] = []
    group_parts: list[str] = []

    if plan.time_dimension:
        grain = GRAIN_SQL.get(plan.time_grain or "month", "month")
        expr = f"date_trunc('{grain}', TRY_CAST({_q(plan.time_dimension)} AS TIMESTAMP))"
        select_parts.append(f"{expr} AS {_q('period')}")
        group_parts.append(expr)

    for d in plan.dimensions:
        select_parts.append(f"{_q(d)} AS {_q(d)}")
        group_parts.append(_q(d))

    for m in plan.metrics:
        template = AGG_SQL[m.aggregation]
        col = _q(m.column)
        if m.aggregation in (Aggregation.sum, Aggregation.avg, Aggregation.median):
            col = f"TRY_CAST({_q(m.column)} AS DOUBLE)"
        select_parts.append(f"{template.format(col=col)} AS {_q(m.label or _alias(m.column))}")

    if not plan.metrics:
        select_parts.append('COUNT(*) AS "Row count"')

    where: list[str] = []
    for f in plan.filters:
        col = _q(f.column)
        if f.op == "is_null":
            where.append(f"{col} IS NULL")
        elif f.op == "not_null":
            where.append(f"{col} IS NOT NULL")
        elif f.op in ("in", "not_in"):
            values = f.value if isinstance(f.value, (list, tuple)) else [f.value]
            if not values:
                continue
            marks = ", ".join(["?"] * len(values))
            where.append(f"{col} {'NOT ' if f.op == 'not_in' else ''}IN ({marks})")
            params.extend(list(values))
        elif f.op == "between":
            values = list(f.value or [])
            if len(values) != 2:
                raise PlanError("A 'between' filter needs exactly two values.")
            where.append(f"{col} BETWEEN ? AND ?")
            params.extend(values)
        else:
            sql_op = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.op]
            where.append(f"{col} {sql_op} ?")
            params.append(f.value)

    # ruff S608 flags string-built SQL, which is correct as a general rule and
    # wrong here: every identifier in `select_parts`, `where` and `group_parts`
    # came from `validate_plan`, which resolves each name against the dataset's
    # real column list and then quotes it, and every user value is appended to
    # `params` and bound by DuckDB rather than interpolated. The test
    # `test_filter_values_are_parameterized` asserts the injection case.
    sql = f"SELECT {', '.join(select_parts)} FROM dataset"  # noqa: S608
    if where:
        sql += " WHERE " + " AND ".join(where)
    if group_parts:
        sql += " GROUP BY " + ", ".join(group_parts)

    if plan.time_dimension:
        # A time series is always read oldest-first, whatever the sort request was.
        order_col, direction = _q("period"), "ASC"
    elif plan.sort_by:
        order_col, direction = _q(plan.sort_by), "DESC" if plan.sort_desc else "ASC"
    elif plan.metrics:
        order_col = _q(plan.metrics[0].label or _alias(plan.metrics[0].column))
        direction = "DESC" if plan.sort_desc else "ASC"
    else:
        order_col, direction = (_q(plan.dimensions[0]), "ASC") if plan.dimensions else (None, "ASC")
    if order_col:
        sql += f" ORDER BY {order_col} {direction} NULLS LAST"
    sql += f" LIMIT {int(plan.limit) + 1}"
    return sql, params


def _run_sql(df: pd.DataFrame, sql: str, params: list[Any]) -> pd.DataFrame:
    con = duckdb.connect()
    timer: threading.Timer | None = None
    try:
        con.register("dataset", df)
        timer = threading.Timer(settings.query_timeout_seconds, con.interrupt)
        timer.daemon = True
        timer.start()
        return con.execute(sql, params).fetch_df()
    except duckdb.InterruptException as exc:  # pragma: no cover - timing dependent
        raise PlanError(f"The query exceeded the {settings.query_timeout_seconds}s limit and was cancelled.") from exc
    except duckdb.Error as exc:
        raise PlanError(f"The query could not be executed: {type(exc).__name__}.") from exc
    finally:
        if timer:
            timer.cancel()
        con.close()


def _jsonable(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = df.replace({np.nan: None})
    records: list[dict[str, Any]] = []
    for row in out.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (pd.Timestamp,)):
                clean[k] = v.isoformat()
            elif isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = None if not np.isfinite(v) else float(v)
            elif isinstance(v, (np.bool_,)):
                clean[k] = bool(v)
            elif v is pd.NaT:
                clean[k] = None
            else:
                clean[k] = v
        records.append(clean)
    return records


def execute_plan(df: pd.DataFrame, plan: AnalysisPlan, semantics: list[ColumnSemantics]) -> PlanResult:
    validated = validate_plan(plan, semantics)
    sql, params = compile_plan(validated)
    started = time.perf_counter()
    result = _run_sql(df, sql, params)
    elapsed = int((time.perf_counter() - started) * 1000)
    truncated = len(result) > validated.limit
    if truncated:
        result = result.head(validated.limit)
    return PlanResult(
        plan=validated,
        columns=[str(c) for c in result.columns],
        rows=_jsonable(result),
        row_count=len(result),
        truncated=truncated,
        executed_ms=elapsed,
        sql_like=sql,
    )


# --------------------------------------------------------------------------- #
# Chart data
# --------------------------------------------------------------------------- #

RAW_CHARTS = {"scatter", "bubble", "box", "histogram", "cluster_scatter"}


def plan_from_chart(spec: ChartSpec, semantics: list[ColumnSemantics]) -> AnalysisPlan:
    by_name = {s.name: s for s in semantics}
    enc = spec.encoding
    x, y = enc.x, enc.y or enc.value
    dims: list[str] = []
    time_dim = None

    for col in (x, enc.series, enc.facet, enc.color):
        if not col or col == "__cluster__" or col not in by_name:
            continue
        if by_name[col].analytical_role == AnalyticalRole.datetime_dimension:
            time_dim = col
        elif col not in dims:
            dims.append(col)

    metrics: list[MetricSpec] = []
    if y and y in by_name and spec.aggregation != Aggregation.none:
        metrics.append(MetricSpec(column=y, aggregation=spec.aggregation, label=by_name[y].label))
    elif y and y in by_name:
        metrics.append(MetricSpec(column=y, aggregation=Aggregation.sum, label=by_name[y].label))

    grain = "month"
    if time_dim:
        gran = next((s for s in semantics if s.name == time_dim), None)
        if gran is not None and gran.parsed_datetime is not None:
            span = gran.parsed_datetime.dropna()
            if not span.empty:
                days = (span.max() - span.min()).days
                grain = "day" if days <= 120 else "week" if days <= 400 else "month" if days <= 2000 else "quarter"

    return AnalysisPlan(
        intent=spec.title,
        metrics=metrics,
        dimensions=dims,
        time_dimension=time_dim,
        time_grain=grain if time_dim else None,
        filters=spec.filters,
        sort_desc=spec.sort != "asc",
        limit=min(spec.limit, settings.query_row_limit),
    )


def chart_data(
    df: pd.DataFrame, spec: ChartSpec, semantics: list[ColumnSemantics]
) -> tuple[list[str], list[dict], bool]:
    """Compute the rows a chart needs. Raw-row charts bypass aggregation."""
    by_name = {s.name: s for s in semantics}
    if spec.chart_type in RAW_CHARTS:
        cols = [
            c for c in (spec.encoding.x, spec.encoding.y, spec.encoding.color, spec.encoding.size) if c and c in by_name
        ]
        if not cols:
            return [], [], False
        sub = df[cols].dropna(how="all").head(min(spec.limit, settings.query_row_limit))
        if spec.chart_type == "cluster_scatter":
            sub = _add_clusters(sub, [c for c in cols if by_name[c].analytical_role in NUMERIC_ROLES])
        return [str(c) for c in sub.columns], _jsonable(sub), len(df) > len(sub)

    plan = plan_from_chart(spec, semantics)
    result = execute_plan(df, plan, semantics)
    return result.columns, result.rows, result.truncated


def _add_clusters(sub: pd.DataFrame, numeric_cols: list[str], k: int = 4) -> pd.DataFrame:
    if len(numeric_cols) < 2 or len(sub) < 20:
        return sub
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    data = sub[numeric_cols].apply(pd.to_numeric, errors="coerce").dropna()
    if len(data) < 20:
        return sub
    scaled = StandardScaler().fit_transform(data)
    labels = KMeans(n_clusters=min(k, max(2, len(data) // 10)), n_init=10, random_state=42).fit_predict(scaled)
    out = sub.copy()
    out["__cluster__"] = pd.Series([f"Segment {int(label) + 1}" for label in labels], index=data.index)
    return out


# --------------------------------------------------------------------------- #
# Heuristic natural-language planner (no LLM required)
# --------------------------------------------------------------------------- #

TIME_WORDS = {
    "day": "day",
    "daily": "day",
    "week": "week",
    "weekly": "week",
    "month": "month",
    "monthly": "month",
    "quarter": "quarter",
    "quarterly": "quarter",
    "year": "year",
    "yearly": "year",
    "annual": "year",
}
TOP_RE = re.compile(r"top\s+(\d{1,3})")
STRIP_SUFFIX_RE = re.compile(
    r"(_amount|_amt|_value|_pct|_percent|_id|_code|_no|_num|_score|_count|_date|_dt|_key|_name)$"
)


def column_aliases(sem: ColumnSemantics) -> set[str]:
    """Words a person might use for this column."""
    raw = sem.name.lower()
    spaced = re.sub(r"[_\-.]+", " ", raw).strip()
    stem = STRIP_SUFFIX_RE.sub("", raw)
    aliases = {raw, spaced, sem.label.lower(), stem, re.sub(r"[_\-.]+", " ", stem).strip()}
    return {a for a in aliases if len(a) >= 3}


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _normalize_words(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return " ".join(_singular(w) for w in words)


def mentioned_columns(question: str, semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    q = f" {_normalize_words(question)} "
    hits: list[tuple[int, ColumnSemantics]] = []
    for sem in semantics:
        best = 0
        for alias in column_aliases(sem):
            normalized = _normalize_words(alias)
            if normalized and f" {normalized} " in q:
                best = max(best, len(normalized))
        if best:
            hits.append((best, sem))
    hits.sort(key=lambda h: -h[0])
    return [sem for _, sem in hits]


def heuristic_plan(question: str, semantics: list[ColumnSemantics], hinted: list[str] | None = None) -> AnalysisPlan:
    """Map a question to a plan by matching column labels and a few keywords."""
    q = question.lower()
    hinted = hinted or []
    by_name = {s.name: s for s in semantics}
    mentioned = mentioned_columns(question, semantics)
    for name in hinted:
        if name in by_name and by_name[name] not in mentioned:
            mentioned.append(by_name[name])

    measures = [s for s in mentioned if s.analytical_role in NUMERIC_ROLES]
    dims = [
        s for s in mentioned if s.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)
    ]
    dates = [s for s in mentioned if s.analytical_role == AnalyticalRole.datetime_dimension]

    if not measures:
        measures = [
            s
            for s in semantics
            if s.analytical_role in NUMERIC_ROLES and s.null_count / max(1, s.null_count + s.non_null_count) < 0.3
        ][:2]
    if not dims and not dates:
        dims = [
            s
            for s in semantics
            if s.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)
            and 2 <= s.distinct_count <= 25
        ][:1]

    wants_time = any(
        w in q for w in ("trend", "over time", "monthly", "growth", "by month", "by year", "seasonal")
    ) or bool(dates)
    if wants_time and not dates:
        dates = [s for s in semantics if s.analytical_role == AnalyticalRole.datetime_dimension][:1]

    grain = next((g for word, g in TIME_WORDS.items() if word in q), "month")
    top = TOP_RE.search(q)
    limit = int(top.group(1)) if top else (200 if dates else 25)

    assumptions = []
    if not mentioned:
        assumptions.append(
            "No column was named, so the most complete measures and a low-cardinality dimension were used."
        )
    if wants_time and dates:
        assumptions.append(f"Time was rolled up to {grain} grain.")

    return AnalysisPlan(
        intent=question.strip()[:200],
        metrics=[
            MetricSpec(
                column=m.name,
                aggregation=m.default_aggregation if m.default_aggregation != Aggregation.none else Aggregation.sum,
                label=m.label,
            )
            for m in measures[:3]
        ],
        dimensions=[d.name for d in dims[:2]],
        time_dimension=dates[0].name if (wants_time and dates) else None,
        time_grain=grain if (wants_time and dates) else None,
        limit=limit,
        assumptions=assumptions,
    )


def chart_for_plan(plan: AnalysisPlan, semantics: list[ColumnSemantics]) -> ChartSpec | None:
    from .chart_rules import build_selection, evaluate_selection

    cols = [m.column for m in plan.metrics] + plan.dimensions + ([plan.time_dimension] if plan.time_dimension else [])
    selection = build_selection(semantics, [c for c in cols if c], row_count=10_000)
    compatible, _ = evaluate_selection(selection)
    if not compatible:
        return None
    preferred = ["line", "multi_line", "bar", "kpi_card", "table"]
    for wanted in preferred:
        for option in compatible:
            if option.chart_type == wanted and option.spec:
                return option.spec
    return compatible[0].spec
