"""Deterministic chart compatibility.

The LLM never decides whether a chart is *possible*; it only ranks and explains
options that these rules have already declared valid for the selected columns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..schemas import (
    Aggregation,
    AnalyticalRole,
    ChartEncoding,
    ChartOption,
    ChartSpec,
    ComplexityTier,
)
from .semantics import ColumnSemantics

NUMERIC_ROLES = {AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage}
CATEGORY_ROLES = {AnalyticalRole.categorical_dimension, AnalyticalRole.geography}
GEO_HINTS = ("country", "region", "state", "province", "city", "postal", "zip", "lat", "lon", "market")


@dataclass
class Selection:
    """A resolved column selection, described in analytical terms."""

    columns: list[ColumnSemantics]
    row_count: int

    @property
    def measures(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.analytical_role in NUMERIC_ROLES]

    @property
    def categories(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.analytical_role in CATEGORY_ROLES]

    @property
    def dates(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.analytical_role == AnalyticalRole.datetime_dimension]

    @property
    def identifiers(self) -> list[ColumnSemantics]:
        return [c for c in self.columns if c.analytical_role == AnalyticalRole.identifier]

    @property
    def geos(self) -> list[ColumnSemantics]:
        return [
            c
            for c in self.columns
            if c.analytical_role == AnalyticalRole.geography or any(h in c.name.lower() for h in GEO_HINTS)
        ]

    @property
    def booleans(self) -> list[ColumnSemantics]:
        return [c for c in self.categories if c.distinct_count == 2]

    def low_card_categories(self, limit: int) -> list[ColumnSemantics]:
        return [c for c in self.categories if 2 <= c.distinct_count <= limit]

    def summary(self) -> str:
        bits = []
        for label, items in (
            ("measure", self.measures),
            ("category", self.categories),
            ("date", self.dates),
            ("identifier", self.identifiers),
        ):
            if items:
                bits.append(
                    f"{len(items)} {label}{'s' if len(items) > 1 else ''} ({', '.join(i.label for i in items)})"
                )
        return "; ".join(bits) or "no chartable columns selected"


@dataclass
class ChartRule:
    chart_type: str
    tier: ComplexityTier
    prerequisites: list[str]
    avoid_when: list[str]
    check: Callable[[Selection], list[str]]
    build: Callable[[Selection], ChartSpec | None]
    rationale: str = ""
    min_rows: int = 1
    extras: list[str] = field(default_factory=list)


def _need(condition: bool, message: str) -> list[str]:
    return [] if condition else [message]


def _first(items, index=0):
    return items[index] if len(items) > index else None


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _kpi(sel: Selection) -> ChartSpec:
    m = sel.measures[0]
    return ChartSpec(
        chart_type="kpi_card",
        title=f"Total {m.label}",
        encoding=ChartEncoding(value=m.name),
        aggregation=m.default_aggregation if m.default_aggregation != Aggregation.none else Aggregation.sum,
        limit=1,
    )


def _bar(sel: Selection) -> ChartSpec:
    c = sel.low_card_categories(50)[0]
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="bar",
        title=f"{m.label if m else 'Row count'} by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name if m else None),
        aggregation=(m.default_aggregation if m and m.default_aggregation != Aggregation.none else Aggregation.sum)
        if m
        else Aggregation.count,
        sort="desc",
        limit=25,
        drill_path=[c.name] + [d.name for d in sel.dates[:1]],
    )


def _line(sel: Selection) -> ChartSpec:
    d = sel.dates[0]
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="line",
        title=f"{m.label if m else 'Row count'} over time",
        encoding=ChartEncoding(x=d.name, y=m.name if m else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=1000,
        drill_path=[d.name],
    )


def _multi_line(sel: Selection) -> ChartSpec:
    d, m, c = sel.dates[0], sel.measures[0], sel.low_card_categories(8)[0]
    return ChartSpec(
        chart_type="multi_line",
        title=f"{m.label} over time by {c.label}",
        encoding=ChartEncoding(x=d.name, y=m.name, series=c.name),
        aggregation=Aggregation.sum,
        limit=2000,
        drill_path=[c.name, d.name],
    )


def _pie(sel: Selection) -> ChartSpec:
    c = sel.low_card_categories(6)[0]
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="donut",
        title=f"Share of {m.label if m else 'rows'} by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name if m else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=6,
    )


def _histogram(sel: Selection) -> ChartSpec:
    m = sel.measures[0]
    return ChartSpec(
        chart_type="histogram",
        title=f"Distribution of {m.label}",
        encoding=ChartEncoding(x=m.name),
        aggregation=Aggregation.count,
        limit=50,
    )


def _table(sel: Selection) -> ChartSpec:
    dims = [c.name for c in sel.categories[:3]] + [d.name for d in sel.dates[:1]]
    return ChartSpec(
        chart_type="table",
        title="Detail table",
        encoding=ChartEncoding(x=dims[0] if dims else None, y=_first(sel.measures).name if sel.measures else None),
        aggregation=Aggregation.sum if sel.measures else Aggregation.count,
        limit=200,
        notes=["Use as the drill-through target from any summary visual."],
    )


def _stacked(sel: Selection, hundred: bool = False) -> ChartSpec:
    cats = sel.low_card_categories(25)
    series = sel.low_card_categories(8)
    outer = cats[0]
    inner = next((c for c in series if c.name != outer.name), None)
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="stacked_bar_100" if hundred else "stacked_bar",
        title=f"{m.label if m else 'Rows'} by {outer.label} and {inner.label if inner else ''}".strip(),
        encoding=ChartEncoding(x=outer.name, y=m.name if m else None, series=inner.name if inner else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=25,
        drill_path=[outer.name] + ([inner.name] if inner else []),
    )


def _scatter(sel: Selection) -> ChartSpec:
    m1, m2 = sel.measures[0], sel.measures[1]
    color = _first(sel.low_card_categories(8))
    return ChartSpec(
        chart_type="scatter",
        title=f"{m1.label} vs {m2.label}",
        encoding=ChartEncoding(x=m1.name, y=m2.name, color=color.name if color else None),
        aggregation=Aggregation.none,
        limit=5000,
    )


def _box(sel: Selection) -> ChartSpec:
    c, m = sel.low_card_categories(20)[0], sel.measures[0]
    return ChartSpec(
        chart_type="box",
        title=f"{m.label} spread by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name),
        aggregation=Aggregation.none,
        limit=20,
    )


def _heatmap(sel: Selection) -> ChartSpec:
    cats = sel.low_card_categories(30)
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="heatmap",
        title=f"{m.label if m else 'Rows'} by {cats[0].label} and {cats[1].label}",
        encoding=ChartEncoding(x=cats[0].name, y=cats[1].name, value=m.name if m else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=900,
    )


def _pareto(sel: Selection) -> ChartSpec:
    c, m = sel.low_card_categories(60)[0], sel.measures[0]
    return ChartSpec(
        chart_type="pareto",
        title=f"Pareto of {m.label} by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name),
        aggregation=Aggregation.sum,
        sort="desc",
        limit=30,
    )


def _map(sel: Selection) -> ChartSpec:
    g = sel.geos[0]
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="map",
        title=f"{m.label if m else 'Rows'} by {g.label}",
        encoding=ChartEncoding(x=g.name, y=m.name if m else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=300,
        notes=["Geographic names must match the map provider's naming before publishing."],
    )


def _pivot(sel: Selection) -> ChartSpec:
    cats = sel.low_card_categories(40)
    m = sel.measures[0]
    return ChartSpec(
        chart_type="pivot_table",
        title=f"{m.label} pivot",
        encoding=ChartEncoding(x=cats[0].name, y=cats[1].name if len(cats) > 1 else None, value=m.name),
        aggregation=m.default_aggregation if m.default_aggregation != Aggregation.none else Aggregation.sum,
        limit=400,
        drill_path=[c.name for c in cats[:2]],
    )


def _waterfall(sel: Selection) -> ChartSpec:
    c, m = sel.low_card_categories(20)[0], sel.measures[0]
    return ChartSpec(
        chart_type="waterfall",
        title=f"Contribution to {m.label} by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name),
        aggregation=Aggregation.sum,
        sort="desc",
        limit=15,
        notes=["Define the comparison base (prior period, plan or budget) before publishing."],
    )


def _funnel(sel: Selection) -> ChartSpec:
    c = sel.low_card_categories(12)[0]
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="funnel",
        title=f"Stage progression by {c.label}",
        encoding=ChartEncoding(x=c.name, y=m.name if m else None),
        aggregation=Aggregation.count_distinct if not m else Aggregation.sum,
        limit=12,
        notes=["Valid only when the category values are ordered stages of one process."],
    )


def _cohort(sel: Selection) -> ChartSpec:
    d = sel.dates[0]
    c = _first(sel.low_card_categories(20))
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="cohort_heatmap",
        title="Cohort retention",
        encoding=ChartEncoding(x=d.name, y=c.name if c else None, value=m.name if m else None),
        aggregation=Aggregation.count_distinct,
        limit=400,
        notes=["Requires an entity key and a first-seen date; confirm both before relying on the result."],
    )


def _small_multiples(sel: Selection) -> ChartSpec:
    d, m, c = sel.dates[0], sel.measures[0], sel.low_card_categories(12)[0]
    return ChartSpec(
        chart_type="small_multiples",
        title=f"{m.label} trend per {c.label}",
        encoding=ChartEncoding(x=d.name, y=m.name, facet=c.name),
        aggregation=Aggregation.sum,
        limit=2000,
    )


def _cluster(sel: Selection) -> ChartSpec:
    m1, m2 = sel.measures[0], sel.measures[1]
    return ChartSpec(
        chart_type="cluster_scatter",
        title=f"Segments by {m1.label} and {m2.label}",
        encoding=ChartEncoding(x=m1.name, y=m2.name, color="__cluster__"),
        aggregation=Aggregation.none,
        limit=5000,
        notes=["K-means on standardised measures; cluster labels are statistical, not business segments."],
    )


def _forecast(sel: Selection) -> ChartSpec:
    d, m = sel.dates[0], sel.measures[0]
    return ChartSpec(
        chart_type="forecast_line",
        title=f"{m.label}: actual and projection",
        encoding=ChartEncoding(x=d.name, y=m.name),
        aggregation=Aggregation.sum,
        limit=1000,
        notes=["Baseline projection only. Publish with backtest error before any commitment is made on it."],
    )


def _driver(sel: Selection) -> ChartSpec:
    m = sel.measures[0]
    return ChartSpec(
        chart_type="driver_bar",
        title=f"Factors associated with {m.label}",
        encoding=ChartEncoding(y=m.name),
        aggregation=Aggregation.none,
        limit=15,
        notes=["Association strengths only. Label every bar as correlation, not cause."],
    )


def _anomaly_timeline(sel: Selection) -> ChartSpec:
    d, m = sel.dates[0], sel.measures[0]
    return ChartSpec(
        chart_type="anomaly_timeline",
        title=f"{m.label} with out-of-band periods",
        encoding=ChartEncoding(x=d.name, y=m.name),
        aggregation=Aggregation.sum,
        limit=1000,
        notes=["Bands are ±3 robust standard deviations of the period-over-period change."],
    )


def _scorecard(sel: Selection) -> ChartSpec:
    return ChartSpec(
        chart_type="scorecard",
        title="Executive scorecard",
        encoding=ChartEncoding(
            value=sel.measures[0].name,
            x=_first(sel.low_card_categories(12)).name if sel.low_card_categories(12) else None,
        ),
        aggregation=Aggregation.sum,
        limit=20,
        notes=["Thresholds and targets must come from the business, not from the data."],
    )


def _sankey(sel: Selection) -> ChartSpec:
    cats = sel.low_card_categories(15)
    m = _first(sel.measures)
    return ChartSpec(
        chart_type="sankey",
        title=f"Flow from {cats[0].label} to {cats[1].label}",
        encoding=ChartEncoding(x=cats[0].name, y=cats[1].name, value=m.name if m else None),
        aggregation=Aggregation.sum if m else Aggregation.count,
        limit=200,
        notes=["Only meaningful when the two columns are a genuine from/to transition."],
    )


def _decomposition(sel: Selection) -> ChartSpec:
    m = sel.measures[0]
    cats = sel.low_card_categories(25)
    return ChartSpec(
        chart_type="decomposition_tree",
        title=f"{m.label} decomposition",
        encoding=ChartEncoding(value=m.name, x=cats[0].name, series=cats[1].name if len(cats) > 1 else None),
        aggregation=Aggregation.sum,
        limit=200,
        drill_path=[c.name for c in cats[:3]],
    )


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

CATALOG: list[ChartRule] = [
    ChartRule(
        "kpi_card",
        "easy",
        ["one measure"],
        ["no measure selected", "the measure is a ratio that cannot be summed"],
        lambda s: _need(len(s.measures) >= 1, "Select at least one measure."),
        _kpi,
        "A single governed number is the fastest way to state performance.",
    ),
    ChartRule(
        "bar",
        "easy",
        ["one dimension with 2-50 members", "optionally one measure"],
        ["more than about 25 bars", "a time dimension, where a line reads better"],
        lambda s: _need(
            len(s.low_card_categories(50)) >= 1, "Select a categorical dimension with 50 or fewer members."
        ),
        _bar,
        "Ranked bars are the most accurately read comparison across categories.",
    ),
    ChartRule(
        "line",
        "easy",
        ["one date or datetime dimension", "one measure or a row count"],
        ["fewer than 3 distinct periods", "large calendar gaps without a date dimension"],
        lambda s: _need(len(s.dates) >= 1, "Select a date or datetime column."),
        _line,
        "A single series over time answers 'is this getting better or worse'.",
    ),
    ChartRule(
        "donut",
        "easy",
        ["one dimension with 6 or fewer members", "parts that genuinely sum to a whole"],
        ["negative values", "more than 6 slices", "comparing change over time"],
        lambda s: _need(len(s.low_card_categories(6)) >= 1, "Part-to-whole needs a dimension with 6 or fewer members."),
        _pie,
        "Acceptable only for a small, additive composition.",
    ),
    ChartRule(
        "histogram",
        "easy",
        ["one measure", "at least 30 rows"],
        ["a measure with fewer than 5 distinct values"],
        lambda s: (
            _need(len(s.measures) >= 1, "Select a measure.") + _need(s.row_count >= 30, "At least 30 rows are needed.")
        ),
        _histogram,
        "Shows shape, spread and outliers that an average hides.",
        min_rows=30,
    ),
    ChartRule(
        "table",
        "easy",
        ["any columns"],
        ["as a substitute for a summary visual on an executive page"],
        lambda s: _need(len(s.columns) >= 1, "Select at least one column."),
        _table,
        "The drill-through destination that makes every other visual auditable.",
    ),
    ChartRule(
        "multi_line",
        "medium",
        ["one date", "one measure", "one dimension with 8 or fewer members"],
        ["more than about 6 series on one axis"],
        lambda s: (
            _need(len(s.dates) >= 1, "Select a date column.")
            + _need(len(s.measures) >= 1, "Select a measure.")
            + _need(len(s.low_card_categories(8)) >= 1, "Series need a dimension with 8 or fewer members.")
        ),
        _multi_line,
        "Compares a handful of series without splitting the page.",
    ),
    ChartRule(
        "stacked_bar",
        "medium",
        ["two dimensions (one with 8 or fewer members)", "one measure"],
        ["comparing the size of inner segments across bars", "negative values"],
        lambda s: _need(len(s.low_card_categories(25)) >= 2, "Two categorical dimensions are needed."),
        lambda s: _stacked(s, False),
        "Good for totals with composition, poor for comparing the middle segments.",
    ),
    ChartRule(
        "stacked_bar_100",
        "medium",
        ["two dimensions", "one additive measure"],
        ["when absolute size matters", "small denominators per bar"],
        lambda s: _need(len(s.low_card_categories(25)) >= 2, "Two categorical dimensions are needed."),
        lambda s: _stacked(s, True),
        "Shows mix shift; hides volume, so pair it with a total.",
    ),
    ChartRule(
        "scatter",
        "medium",
        ["two measures", "at least 30 rows"],
        ["heavy overplotting without aggregation or opacity"],
        lambda s: (
            _need(len(s.measures) >= 2, "Two measures are needed.")
            + _need(s.row_count >= 30, "At least 30 rows are needed.")
        ),
        _scatter,
        "Reveals the relationship, clusters and outliers between two measures.",
        min_rows=30,
    ),
    ChartRule(
        "box",
        "medium",
        ["one dimension with 20 or fewer members", "one measure", "at least 50 rows"],
        ["very small groups, where individual points are clearer"],
        lambda s: (
            _need(len(s.low_card_categories(20)) >= 1, "A dimension with 20 or fewer members is needed.")
            + _need(len(s.measures) >= 1, "Select a measure.")
            + _need(s.row_count >= 50, "At least 50 rows are needed.")
        ),
        _box,
        "Compares distributions, not just averages, across groups.",
        min_rows=50,
    ),
    ChartRule(
        "heatmap",
        "medium",
        ["two dimensions with moderate cardinality", "one measure or a count"],
        ["when precise values must be read off the chart"],
        lambda s: _need(len(s.low_card_categories(30)) >= 2, "Two moderate-cardinality dimensions are needed."),
        _heatmap,
        "Finds dense and empty pockets across a two-dimensional grid.",
    ),
    ChartRule(
        "pareto",
        "medium",
        ["one dimension", "one additive measure"],
        ["non-additive measures such as rates"],
        lambda s: (
            _need(len(s.low_card_categories(60)) >= 1, "A categorical dimension is needed.")
            + _need(len(s.measures) >= 1, "An additive measure is needed.")
        ),
        _pareto,
        "Quantifies the concentration behind the 80/20 claim.",
    ),
    ChartRule(
        "map",
        "medium",
        ["a recognised geographic column"],
        ["postcode-level detail without a matching boundary file"],
        lambda s: _need(len(s.geos) >= 1, "A geographic column is needed."),
        _map,
        "Only worth the space when location itself drives the decision.",
    ),
    ChartRule(
        "pivot_table",
        "medium",
        ["one or two dimensions", "one measure"],
        ["as the only visual on an executive page"],
        lambda s: (
            _need(len(s.low_card_categories(40)) >= 1, "At least one dimension is needed.")
            + _need(len(s.measures) >= 1, "Select a measure.")
        ),
        _pivot,
        "The familiar OBIEE-style crosstab with prompts and drill-down.",
    ),
    ChartRule(
        "waterfall",
        "complex",
        ["one dimension", "one additive measure", "an agreed comparison base"],
        ["without a defined baseline, plan or prior period"],
        lambda s: (
            _need(len(s.low_card_categories(20)) >= 1, "A dimension with 20 or fewer members is needed.")
            + _need(len(s.measures) >= 1, "An additive measure is needed.")
        ),
        _waterfall,
        "Turns 'we missed the number' into 'here is which part missed it'.",
    ),
    ChartRule(
        "funnel",
        "complex",
        ["an ordered stage dimension", "a countable entity"],
        ["categories that are not sequential stages"],
        lambda s: _need(
            len(s.low_card_categories(12)) >= 1, "A stage-like dimension with 12 or fewer members is needed."
        ),
        _funnel,
        "Locates the drop-off in a process, once stage order is confirmed.",
    ),
    ChartRule(
        "cohort_heatmap",
        "complex",
        ["a date dimension", "an entity key", "at least 3 periods of history"],
        ["without a reliable first-seen date per entity"],
        lambda s: (
            _need(len(s.dates) >= 1, "A date column is needed.")
            + _need(len(s.identifiers) >= 1 or len(s.categories) >= 1, "An entity key or grouping column is needed.")
            + _need(s.row_count >= 200, "Cohort work needs at least 200 rows.")
        ),
        _cohort,
        "Separates genuine retention from growth in new sign-ups.",
        min_rows=200,
    ),
    ChartRule(
        "small_multiples",
        "complex",
        ["one date", "one measure", "a facet dimension with 12 or fewer members"],
        ["when the panels become too small to read"],
        lambda s: (
            _need(len(s.dates) >= 1, "A date column is needed.")
            + _need(len(s.measures) >= 1, "Select a measure.")
            + _need(len(s.low_card_categories(12)) >= 1, "A facet dimension with 12 or fewer members is needed.")
        ),
        _small_multiples,
        "Compares many trends without overplotting a single axis.",
    ),
    ChartRule(
        "decomposition_tree",
        "complex",
        ["one measure", "two or more nested dimensions"],
        ["when dimensions are not genuinely hierarchical"],
        lambda s: (
            _need(len(s.measures) >= 1, "Select a measure.")
            + _need(len(s.low_card_categories(25)) >= 2, "At least two dimensions are needed.")
        ),
        _decomposition,
        "Guided root-cause walk from a total down to the contributing members.",
    ),
    ChartRule(
        "cluster_scatter",
        "complex",
        ["two or more measures", "at least 100 rows"],
        ["when the business already has an agreed segmentation"],
        lambda s: (
            _need(len(s.measures) >= 2, "Two measures are needed.")
            + _need(s.row_count >= 100, "At least 100 rows are needed.")
        ),
        _cluster,
        "Proposes data-driven segments for the business to accept or reject.",
        min_rows=100,
    ),
    ChartRule(
        "forecast_line",
        "complex",
        ["one date with regular grain", "one measure", "at least 24 periods"],
        ["fewer than two seasonal cycles of history", "after a structural break"],
        lambda s: (
            _need(len(s.dates) >= 1, "A date column is needed.")
            + _need(len(s.measures) >= 1, "Select a measure.")
            + _need(s.row_count >= 100, "At least 100 rows are needed for a credible projection.")
        ),
        _forecast,
        "Sets an expectation and makes variance visible early.",
        min_rows=100,
    ),
    ChartRule(
        "driver_bar",
        "very_complex",
        ["one target measure", "several candidate dimensions or measures"],
        ["as evidence of causation", "with heavy missingness in the target"],
        lambda s: (
            _need(len(s.measures) >= 1, "A target measure is needed.")
            + _need(len(s.columns) >= 3, "Select the target plus at least two candidate drivers.")
        ),
        _driver,
        "Ranks what moves with the target, with explicit association-not-cause framing.",
    ),
    ChartRule(
        "anomaly_timeline",
        "very_complex",
        ["one date", "one measure", "enough history to set a band"],
        ["on sparse or highly intermittent series"],
        lambda s: (
            _need(len(s.dates) >= 1, "A date column is needed.")
            + _need(len(s.measures) >= 1, "Select a measure.")
            + _need(s.row_count >= 100, "At least 100 rows are needed.")
        ),
        _anomaly_timeline,
        "Moves the team from reading dashboards to being alerted by them.",
        min_rows=100,
    ),
    ChartRule(
        "scorecard",
        "very_complex",
        ["several measures", "at least one dimension", "business-agreed targets"],
        ["without owners and thresholds for each metric"],
        lambda s: (
            _need(len(s.measures) >= 2, "At least two measures are needed.")
            + _need(len(s.low_card_categories(12)) >= 1, "A dimension to score against is needed.")
        ),
        _scorecard,
        "One page that tells an executive where to look first.",
    ),
    ChartRule(
        "sankey",
        "very_complex",
        ["a from-column and a to-column describing real transitions"],
        ["two unrelated attributes of the same row"],
        lambda s: _need(len(s.low_card_categories(15)) >= 2, "Two moderate-cardinality dimensions are needed."),
        _sankey,
        "Only justified when the data really encodes movement between states.",
    ),
]

RULES_BY_TYPE = {r.chart_type: r for r in CATALOG}
TIER_ORDER = {"easy": 0, "medium": 1, "complex": 2, "very_complex": 3}


MAX_SELECTION = 8


def build_fingerprint(dataset_id: str, column_ids: list[str]) -> str:
    """ENH-03: the one definition of selection identity.

    Deliberately a readable, order-sensitive string rather than a hash, so the
    frontend can compute the identical value without hashing and a mismatch is
    obvious in a log or a network trace.
    """
    return f"{dataset_id}|{'>'.join(column_ids)}"


def canonical_selection(names: list[str], semantics: list[ColumnSemantics]) -> tuple[list[str], list[str], bool]:
    """Resolve a requested selection to stable, ordered, existing column ids.

    Returns (column_ids, dropped, truncated). Order is the user's order, which
    is what the UI shows, and duplicates are collapsed so a count can never
    disagree between the badge and the payload.
    """
    known = {s.name for s in semantics}
    lower = {s.name.lower(): s.name for s in semantics}
    ordered: list[str] = []
    dropped: list[str] = []
    for name in names:
        resolved = name if name in known else lower.get(str(name).lower())
        if resolved is None:
            dropped.append(str(name))
            continue
        if resolved not in ordered:
            ordered.append(resolved)
    truncated = len(ordered) > MAX_SELECTION
    return ordered[:MAX_SELECTION], dropped, truncated


def build_selection(semantics: list[ColumnSemantics], names: list[str], row_count: int) -> Selection:
    by_name = {s.name: s for s in semantics}
    ordered, _, _ = canonical_selection(list(names), semantics)
    chosen = [by_name[n] for n in ordered]
    return Selection(columns=chosen, row_count=row_count)


def evaluate_selection(selection: Selection) -> tuple[list[ChartOption], list[ChartOption]]:
    """Return (compatible, rejected) options for a column selection."""
    compatible: list[ChartOption] = []
    rejected: list[ChartOption] = []
    for rule in CATALOG:
        blockers = rule.check(selection)
        if selection.row_count < rule.min_rows:
            blockers.append(f"Needs at least {rule.min_rows} rows.")
        option = ChartOption(
            chart_type=rule.chart_type,
            tier=rule.tier,
            compatible=not blockers,
            rationale=rule.rationale,
            prerequisites=rule.prerequisites,
            avoid_when=rule.avoid_when,
            blockers=blockers,
        )
        if blockers:
            rejected.append(option)
            continue
        try:
            option.spec = rule.build(selection)
        except Exception as exc:  # a builder guard failed: treat as incompatible
            option.compatible = False
            option.blockers = [f"Specification could not be built ({type(exc).__name__})."]
            rejected.append(option)
            continue
        compatible.append(option)

    compatible.sort(key=lambda o: TIER_ORDER[o.tier])
    for i, option in enumerate(compatible, start=1):
        option.rank = i
    return compatible, rejected


def validate_chart_spec(spec: ChartSpec, semantics: list[ColumnSemantics], row_count: int) -> tuple[bool, list[str]]:
    """Check a chart spec (typically LLM-authored) against the real dataset."""
    problems: list[str] = []
    by_name = {s.name: s for s in semantics}
    used = [v for v in spec.encoding.model_dump().values() if v]
    for col in used:
        if col == "__cluster__":
            continue
        if col not in by_name:
            problems.append(f"Column '{col}' does not exist in this dataset.")

    rule = RULES_BY_TYPE.get(spec.chart_type)
    if rule is None:
        problems.append(f"Chart type '{spec.chart_type}' is not supported.")
        return False, problems
    if problems:
        return False, problems

    selection = build_selection(semantics, [c for c in used if c != "__cluster__"], row_count)
    blockers = rule.check(selection)
    if selection.row_count < rule.min_rows:
        blockers.append(f"Needs at least {rule.min_rows} rows.")
    problems.extend(blockers)

    y = spec.encoding.y or spec.encoding.value
    if y and y in by_name and spec.aggregation not in (Aggregation.none, Aggregation.count, Aggregation.count_distinct):
        sem = by_name[y]
        if sem.analytical_role not in NUMERIC_ROLES:
            problems.append(
                f"'{sem.label}' is a {sem.analytical_role.value}, so "
                f"'{spec.aggregation.value}' is not a valid aggregation."
            )
        elif sem.analytical_role == AnalyticalRole.percentage and spec.aggregation == Aggregation.sum:
            problems.append(f"'{sem.label}' is a rate; summing it produces a meaningless total. Use avg.")
    return not problems, problems


def repair_chart_spec(
    spec: ChartSpec, semantics: list[ColumnSemantics], row_count: int
) -> tuple[ChartSpec | None, list[str]]:
    """Best-effort fix for a near-miss spec. Returns (spec, notes) or (None, notes)."""
    notes: list[str] = []
    by_name = {s.name: s for s in semantics}
    enc = spec.encoding.model_copy()

    for field_name, value in enc.model_dump().items():
        if value and value != "__cluster__" and value not in by_name:
            match = next((n for n in by_name if n.lower() == str(value).lower()), None)
            if match:
                setattr(enc, field_name, match)
                notes.append(f"Matched '{value}' to the existing column '{match}'.")
            else:
                setattr(enc, field_name, None)
                notes.append(f"Dropped unknown column '{value}'.")

    candidate = spec.model_copy(update={"encoding": enc})
    y = enc.y or enc.value
    if (
        y in by_name
        and by_name[y].analytical_role == AnalyticalRole.percentage
        and candidate.aggregation == Aggregation.sum
    ):
        candidate = candidate.model_copy(update={"aggregation": Aggregation.avg})
        notes.append(f"Changed the aggregation on '{by_name[y].label}' from sum to average because it is a rate.")

    ok, problems = validate_chart_spec(candidate, semantics, row_count)
    if ok:
        return candidate, notes
    notes.extend(problems)
    return None, notes
