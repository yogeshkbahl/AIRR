"""ENH-04 — Quick Ask suggestions generated from the dataset, not hardcoded.

Each suggestion carries a stable id and a structured intent. The backend maps
the intent to a governed analysis plan (or, for driver questions, to the
association engine), so nothing depends on parsing the visible label text. A
suggestion is only emitted when the columns it needs exist and have the right
analytical role, which is what stops "associated with churn" appearing on a
dataset that has no churn column.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Literal

from ..schemas import (
    Aggregation,
    AnalysisPlan,
    AnalyticalRole,
    MetricSpec,
    PlanResult,
    QuickAsk,
    QuickAskIntent,
)
from .relationships import analyse_pair
from .semantics import ColumnSemantics

NUMERIC_ROLES = {AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage}
CATEGORY_ROLES = {AnalyticalRole.categorical_dimension, AnalyticalRole.geography}

#: A ranking is only worth asking for when there are enough members to rank;
#: with fewer, the comparison suggestions already show every member.
MIN_RANK_MEMBERS = 5
TOP_N = 10

QuickAskCategory = Literal["kpi", "comparison", "trend", "ranking", "distribution", "relationship"]


def _stable_id(kind: str, columns: list[str]) -> str:
    digest = hashlib.sha256("|".join([kind, *sorted(columns)]).encode("utf-8")).hexdigest()
    return f"qa-{kind}-{digest[:8]}"


def _rank_measures(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    from ..llm.service import rank_measures

    return rank_measures(semantics)


def _rank_dimensions(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    from ..llm.service import rank_dimensions

    return rank_dimensions(semantics)


def generate_quick_asks(semantics: list[ColumnSemantics], row_count: int) -> list[QuickAsk]:
    """Build the shortlist. Order is deterministic, so ids are stable per dataset."""
    measures = _rank_measures(semantics)
    dims = _rank_dimensions(semantics)
    dates = [s for s in semantics if s.analytical_role == AnalyticalRole.datetime_dimension]
    rankable = sorted(
        (
            s
            for s in semantics
            if s.analytical_role in CATEGORY_ROLES and MIN_RANK_MEMBERS <= s.distinct_count <= 500
        ),
        key=lambda s: (-s.distinct_count, s.position),
    )
    entity = _entity(semantics)
    # Spread the comparisons over different dimensions when the data has them.
    count_dim = dims[0] if dims else None
    measure_dim = dims[1] if len(dims) > 1 else count_dim

    out: list[QuickAsk] = []

    def add(
        kind: str,
        category: QuickAskCategory,
        label: str,
        question: str,
        intent: QuickAskIntent,
        columns: list[str],
    ) -> None:
        out.append(
            QuickAsk(
                id=_stable_id(kind, columns),
                label=label,
                question=question,
                category=category,
                intent=intent,
                required_columns=columns,
            )
        )

    if measures:
        top = measures[:3]
        kpis = [_metric(m) for m in top]
        names = [_lower_first(k.label) for k in kpis]
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        add(
            "kpi_overview",
            "kpi",
            "Headline KPIs",
            f"What are the headline figures: {listed}?",
            QuickAskIntent(
                kind="kpi_overview",
                metrics=kpis,
                limit=1,
            ),
            [m.name for m in top],
        )

    if count_dim is not None:
        noun, count_metrics, count_columns = _entity_count(entity)
        add(
            "count_by_dimension",
            "comparison",
            f"{noun.capitalize()} by {count_dim.label}",
            f"How many {noun} are there by {count_dim.label}?",
            QuickAskIntent(
                kind="count_by_dimension",
                metrics=count_metrics,
                dimensions=[count_dim.name],
                limit=count_dim.distinct_count,
            ),
            [*count_columns, count_dim.name],
        )

    if measures and measure_dim is not None:
        measure = measures[0]
        metric = _metric(measure)
        add(
            "measure_by_dimension",
            "comparison",
            f"{metric.label} by {measure_dim.label}",
            f"What is {_lower_first(metric.label)} by {measure_dim.label}?",
            QuickAskIntent(
                kind="measure_by_dimension",
                metrics=[metric],
                dimensions=[measure_dim.name],
                limit=measure_dim.distinct_count,
            ),
            [measure.name, measure_dim.name],
        )

    if measures and dates:
        measure, date = measures[0], dates[0]
        grain = _grain_for(date, row_count)
        add(
            "trend",
            "trend",
            f"{_metric(measure).label} over time",
            f"How has {_lower_first(_metric(measure).label)} moved by {grain}?",
            QuickAskIntent(
                kind="trend",
                metrics=[_metric(measure)],
                time_dimension=date.name,
                time_grain=grain,
                limit=200,
            ),
            [measure.name, date.name],
        )

    if measures and rankable:
        measure, dimension = measures[0], rankable[0]
        metric = _metric(measure)
        members = dimension.distinct_count
        if members > TOP_N:
            label = f"Top {TOP_N} {dimension.label} by {_lower_first(metric.label)}"
            question = f"Which {TOP_N} {dimension.label} values have the highest {_lower_first(metric.label)}?"
        else:
            # Fewer members than a "top N" would promise: rank all of them.
            label = f"{dimension.label} ranked by {_lower_first(metric.label)}"
            question = f"How do all {members} {dimension.label} values rank on {_lower_first(metric.label)}?"
        add(
            "top_n",
            "ranking",
            label,
            question,
            QuickAskIntent(
                kind="top_n",
                metrics=[metric],
                dimensions=[dimension.name],
                limit=min(TOP_N, members),
            ),
            [measure.name, dimension.name],
        )

    pair = _comparable_pair(measures)
    if pair is not None and measure_dim is not None:
        first, second = pair
        first_metric, second_metric = _metric(first), _metric(second)
        add(
            "compare_measures",
            "comparison",
            f"{first_metric.label} vs {_lower_first(second_metric.label)}",
            f"How do {_lower_first(first_metric.label)} and {_lower_first(second_metric.label)} "
            f"compare by {measure_dim.label}?",
            QuickAskIntent(
                kind="compare_measures",
                metrics=[first_metric, second_metric],
                dimensions=[measure_dim.name],
                limit=measure_dim.distinct_count,
            ),
            [first.name, second.name, measure_dim.name],
        )

    if measures:
        measure = measures[0]
        name = _lower_first(measure.label)
        add(
            "distribution",
            "distribution",
            f"Spread of {name}",
            f"How is {name} distributed?",
            QuickAskIntent(
                kind="distribution",
                metrics=[
                    MetricSpec(column=measure.name, aggregation=Aggregation.min, label=f"Minimum {name}"),
                    MetricSpec(column=measure.name, aggregation=Aggregation.median, label=f"Median {name}"),
                    MetricSpec(column=measure.name, aggregation=Aggregation.avg, label=f"Average {name}"),
                    MetricSpec(column=measure.name, aggregation=Aggregation.max, label=f"Maximum {name}"),
                ],
                limit=1,
            ),
            [measure.name],
        )

    # A driver question is only offered when there is something to correlate
    # against, and it names the real target column rather than a generic noun.
    target = _driver_target(semantics, measures)
    if target is not None and len(semantics) >= 3:
        name = _lower_first(target.label)
        if target.analytical_role in CATEGORY_ROLES:
            label, question = f"What sets {name} groups apart?", f"Which factors differ most across {name}?"
        else:
            label, question = f"What moves {name}?", f"Which factors appear most associated with {name}?"
        add(
            "drivers",
            "relationship",
            label,
            question,
            QuickAskIntent(kind="drivers", target_column=target.name, limit=8),
            [target.name],
        )

    return out


#: Leading words a column label may already carry for its own aggregation, so
#: `avg_transaction_value` reads "Average transaction value", not "Average Avg…".
_AGG_WORDS = {
    Aggregation.sum: {"total", "sum"},
    Aggregation.avg: {"avg", "average", "mean"},
    Aggregation.median: {"median"},
}


def _metric(column: ColumnSemantics) -> MetricSpec:
    aggregation = (
        column.default_aggregation if column.default_aggregation not in (Aggregation.none,) else Aggregation.sum
    )
    prefix = {
        Aggregation.sum: "Total",
        Aggregation.avg: "Average",
        Aggregation.median: "Median",
        Aggregation.count_distinct: "Distinct",
    }.get(aggregation, aggregation.value.title())
    words = column.label.split()
    if len(words) > 1 and words[0].lower() in _AGG_WORDS.get(aggregation, set()):
        words = words[1:]
    rest = _lower_first(" ".join(words))
    return MetricSpec(column=column.name, aggregation=aggregation, label=f"{prefix} {rest}")


def _lower_first(text: str) -> str:
    """Lower-case the first letter unless the first word is an acronym."""
    first = text.split(" ", 1)[0]
    if len(first) > 1 and first.isupper():
        return text
    return text[:1].lower() + text[1:]


def _comparable_pair(measures: list[ColumnSemantics]) -> tuple[ColumnSemantics, ColumnSemantics] | None:
    """Two measures side by side only when they share a unit and an aggregation.

    Revenue next to margin is a comparison; revenue next to age is not.
    """
    for i, first in enumerate(measures):
        for second in measures[i + 1 :]:
            if (
                first.analytical_role == second.analytical_role
                and first.analytical_role in (AnalyticalRole.currency, AnalyticalRole.percentage)
                and first.default_aggregation == second.default_aggregation
            ):
                return first, second
    return None


def _entity(semantics: list[ColumnSemantics]) -> ColumnSemantics | None:
    """The identifier that names what one row is, e.g. `order_id` for orders."""
    candidates = [
        s
        for s in semantics
        if s.analytical_role == AnalyticalRole.identifier
        and s.uniqueness_ratio >= 0.9
        and re.search(r"(^|_)(id|key|no|number)$", s.name.lower())
        and _entity_noun(s.name)
    ]
    return max(candidates, key=lambda s: (s.uniqueness_ratio, -s.position), default=None)


def _entity_noun(name: str) -> str:
    stem = re.sub(r"[_\s-]*(id|key|no|number)$", "", name.strip(), flags=re.IGNORECASE)
    stem = re.sub(r"[_\-.]+", " ", stem).strip().lower()
    if not stem:
        return ""
    if stem.endswith("y") and not stem.endswith(("ay", "ey", "oy", "uy")):
        return stem[:-1] + "ies"
    if stem.endswith(("s", "x", "ch", "sh")):
        return stem + "es"
    return stem + "s"


def _entity_count(entity: ColumnSemantics | None) -> tuple[str, list[MetricSpec], list[str]]:
    """Count the row's entity when one is identifiable, else count records."""
    if entity is None:
        return "records", [], []
    noun = _entity_noun(entity.name)
    metric = MetricSpec(column=entity.name, aggregation=Aggregation.count_distinct, label=noun.capitalize())
    return noun, [metric], [entity.name]


def _grain_for(date: ColumnSemantics, row_count: int) -> str:
    if date.parsed_datetime is None:
        return "month"
    values = date.parsed_datetime.dropna()
    if values.empty:
        return "month"
    days = (values.max() - values.min()).days
    if days <= 120:
        return "day"
    if days <= 400:
        return "week"
    if days <= 2000:
        return "month"
    return "quarter"


def _driver_target(semantics: list[ColumnSemantics], measures: list[ColumnSemantics]) -> ColumnSemantics | None:
    """Prefer an obvious outcome flag, else the headline measure."""
    for column in semantics:
        name = column.name.lower()
        outcome_word = any(
            word in name
            for word in ("churn", "attrition", "default", "fraud", "conversion", "converted", "retained", "segment",
                         "outcome", "target")
        )
        usable = column.analytical_role in CATEGORY_ROLES | NUMERIC_ROLES and column.distinct_count >= 2
        if outcome_word and usable:
            return column
    return measures[0] if measures else None


def plan_for_intent(intent: QuickAskIntent, question: str) -> AnalysisPlan:
    """Deterministic intent to plan mapping. No text parsing, no model."""
    return AnalysisPlan(
        intent=question[:200],
        metrics=list(intent.metrics),
        dimensions=list(intent.dimensions),
        time_dimension=intent.time_dimension,
        time_grain=intent.time_grain,  # type: ignore[arg-type]
        sort_desc=True,
        limit=intent.limit,
        assumptions=[f"Quick Ask '{intent.kind}' used the dataset's inferred roles and default aggregations."],
    )


def driver_result(df, semantics: list[ColumnSemantics], target: str, limit: int = 8) -> PlanResult:
    """Rank association strength against a target column, using the engine.

    This is not SQL, and the result says so, because presenting association
    output as if it were an aggregate query would be misleading.
    """
    started = time.perf_counter()
    by_name = {s.name: s for s in semantics}
    if target not in by_name:
        raise KeyError(target)
    anchor = by_name[target]

    rows: list[dict] = []
    for candidate in semantics:
        if candidate.name == target:
            continue
        if candidate.analytical_role in (
            AnalyticalRole.identifier,
            AnalyticalRole.free_text,
            AnalyticalRole.sensitive,
            AnalyticalRole.unusable,
        ):
            continue
        result = analyse_pair(df, anchor, candidate)
        if not result.reliable or result.statistic is None:
            continue
        rows.append(
            {
                "Factor": candidate.label,
                "Method": result.method.replace("_", " "),
                "Association": round(abs(result.statistic), 4),
                "Direction": ("positive" if result.statistic > 0 else "negative" if result.statistic < 0 else "flat")
                if result.method in ("pearson", "spearman_vs_time", "point_biserial")
                else "n/a",
                "Effect": result.effect_size_label or "unclear",
                "Sample": result.sample_size,
            }
        )

    rows.sort(key=lambda row: row["Association"], reverse=True)
    truncated = len(rows) > limit
    rows = rows[:limit]
    plan = AnalysisPlan(
        intent=f"Association strength against {anchor.label}",
        metrics=[],
        dimensions=[],
        limit=limit,
        assumptions=[
            "Association only. A ranked factor is a place to look, not a proven cause.",
            "Each pair uses the statistic appropriate to its two analytical roles.",
        ],
    )
    return PlanResult(
        plan=plan,
        columns=["Factor", "Method", "Association", "Direction", "Effect", "Sample"],
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        executed_ms=int((time.perf_counter() - started) * 1000),
        sql_like=(
            f'-- computed by the relationship engine against "{target}"\n'
            "-- type-aware statistic per pair; no SQL aggregate was executed"
        ),
    )
