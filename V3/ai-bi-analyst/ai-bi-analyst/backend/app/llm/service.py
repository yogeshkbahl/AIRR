"""The only place where model output turns into product behaviour.

Flow for every call: build a compact, privacy-aware profile -> render a versioned
prompt -> parse JSON -> validate against Pydantic -> validate against the real
dataset -> repair or reject. Anything that fails falls back to the deterministic
heuristic narrator so the product never shows invented facts.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from ..core.chart_rules import (
    RULES_BY_TYPE,
    Selection,
    build_selection,
    evaluate_selection,
)
from ..core.semantics import ColumnSemantics
from ..schemas import (
    Aggregation,
    AnalysisPlan,
    AnalyticalRole,
    Anomaly,
    BusinessInterpretation,
    ChartOption,
    ColumnProfile,
    DatasetOverview,
    EvidenceKind,
    ExecutiveAnswer,
    MetricSpec,
    PlanResult,
    RecommendationSet,
    RelationshipResult,
    ReportRecommendation,
)
from . import prompts
from .providers import LLMError, LLMProvider, get_provider
from .usage import TokenUsage, build_record, ledger

NUMERIC_ROLES = {AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage}
CATEGORY_ROLES = {AnalyticalRole.categorical_dimension, AnalyticalRole.geography}
CHART_TYPES = sorted(RULES_BY_TYPE.keys())


# --------------------------------------------------------------------------- #
# Compact profile sent to the model
# --------------------------------------------------------------------------- #


def compact_profile(
    overview: DatasetOverview,
    columns: list[ColumnProfile],
    anomalies: list[Anomaly],
    relationships: list[RelationshipResult],
    *,
    max_columns: int = 60,
) -> dict:
    """Small, safe payload. No raw rows, no sensitive examples, no keys."""
    cols = []
    for c in columns[:max_columns]:
        entry = {
            "name": c.name,
            "label": c.label,
            "role": c.analytical_role.value,
            "type": c.physical_type.value,
            "default_aggregation": c.default_aggregation.value,
            "null_percent": c.null_percent,
            "distinct_count": c.distinct_count,
            "uniqueness_ratio": c.uniqueness_ratio,
        }
        if c.hierarchy:
            entry["hierarchy"] = c.hierarchy
        if c.is_sensitive:
            entry["sensitive"] = True
        elif c.numeric:
            entry["stats"] = {
                "min": c.numeric.min,
                "max": c.numeric.max,
                "mean": c.numeric.mean,
                "median": c.numeric.median,
                "negative_count": c.numeric.negative_count,
            }
        elif c.categorical and c.categorical.top_values:
            entry["top_values"] = [{"value": t.value, "percent": t.percent} for t in c.categorical.top_values[:5]]
        elif c.date:
            entry["date_range"] = {
                "earliest": c.date.earliest,
                "latest": c.date.latest,
                "granularity": c.date.inferred_granularity,
            }
        cols.append(entry)

    return {
        "dataset_id": overview.dataset_id,
        "profile_version": "profile-v1.2",
        "row_count": overview.row_count,
        "column_count": overview.column_count,
        "sampled": overview.sampled,
        "quality_score": overview.quality.score,
        "duplicate_row_percent": overview.duplicate_row_percent,
        "missing_cell_percent": overview.missing_cell_percent,
        "role_counts": overview.role_counts,
        "columns": cols,
        "anomaly_summary": [
            {
                "kind": a.kind,
                "severity": a.severity.value,
                "columns": a.columns,
                "affected_percent": a.affected_percent,
                "title": a.title,
            }
            for a in anomalies[:20]
        ],
        "strongest_relationships": [
            {
                "x": r.column_x,
                "y": r.column_y,
                "method": r.method,
                "statistic": r.statistic,
                "effect": r.effect_size_label,
                "sample_size": r.sample_size,
            }
            for r in relationships[:15]
            if r.reliable and r.statistic is not None
        ],
    }


def _columns_for_prompt(semantics: list[ColumnSemantics]) -> list[dict]:
    return [
        {
            "name": s.name,
            "label": s.label,
            "role": s.analytical_role.value,
            "default_aggregation": s.default_aggregation.value,
            "distinct_count": s.distinct_count,
        }
        for s in semantics
    ]


# --------------------------------------------------------------------------- #
# Heuristic narrator (deterministic, always available)
# --------------------------------------------------------------------------- #


def heuristic_interpretation(
    overview: DatasetOverview, columns: list[ColumnProfile], anomalies: list[Anomaly]
) -> BusinessInterpretation:
    measures = [c for c in columns if c.analytical_role in NUMERIC_ROLES]
    dims = [c for c in columns if c.analytical_role in CATEGORY_ROLES]
    dates = [c for c in columns if c.analytical_role == AnalyticalRole.datetime_dimension]
    ids = [c for c in columns if c.analytical_role == AnalyticalRole.identifier]

    grain_bits = []
    if ids:
        grain_bits.append(f"one row per {ids[0].label}")
    if dates:
        grain_bits.append(f"tracked by {dates[0].label}")
    grain = " ".join(grain_bits) if grain_bits else "grain not determined from the data alone"

    subject = "general operational data"
    names = " ".join(c.name.lower() for c in columns)
    for keywords, label in (
        (("revenue", "sales", "order", "invoice", "price"), "sales and revenue reporting"),
        (("churn", "customer", "subscription", "retention"), "customer lifecycle reporting"),
        (("employee", "headcount", "salary", "attrition"), "workforce reporting"),
        (("ticket", "incident", "sla", "resolution"), "service operations reporting"),
        (("inventory", "stock", "warehouse", "shipment"), "supply chain reporting"),
        (("claim", "premium", "policy"), "insurance reporting"),
    ):
        if any(k in names for k in keywords):
            subject = label
            break

    summary = (
        f"{overview.row_count:,} rows and {overview.column_count} columns, profiled with a data-quality "
        f"score of {overview.quality.score:.0f} ({overview.quality.grade}). "
        f"{len(measures)} measures, {len(dims)} categorical dimensions and {len(dates)} date columns are usable "
        f"for reporting. {overview.missing_cell_percent:.1f}% of cells are empty and "
        f"{overview.duplicate_row_percent:.1f}% of rows are exact duplicates."
    )
    if dates and dates[0].date and dates[0].date.earliest:
        summary += f" {dates[0].label} spans {dates[0].date.earliest[:10]} to {dates[0].date.latest[:10]}."

    return BusinessInterpretation(
        dataset_summary=summary,
        likely_grain=grain,
        likely_subject_area=subject,
        key_measures=[c.name for c in measures[:5]],
        key_dimensions=[c.name for c in (dates + dims)[:5]],
        data_quality_call_outs=[a.title for a in anomalies[:5]],
        open_questions=[
            "Which measure is the agreed headline KPI for this audience?",
            "What is the official definition and filter set for that KPI?",
            "Is the row grain confirmed, and which key enforces it?",
        ],
        evidence_kind=EvidenceKind.inference,
    )


def _recommendation_from_option(
    option: ChartOption, selection: Selection, semantics: list[ColumnSemantics], audience: str, question: str
) -> ReportRecommendation | None:
    spec = option.spec
    if spec is None:
        return None
    by_name = {s.name: s for s in semantics}
    used = [v for v in spec.encoding.model_dump().values() if v and v != "__cluster__"]
    metrics = [
        MetricSpec(
            column=c,
            aggregation=spec.aggregation if spec.aggregation != Aggregation.none else Aggregation.sum,
            label=by_name[c].label,
        )
        for c in used
        if c in by_name and by_name[c].analytical_role in NUMERIC_ROLES
    ]
    dims = [c for c in used if c in by_name and by_name[c].analytical_role not in NUMERIC_ROLES]
    rule = RULES_BY_TYPE[spec.chart_type]
    return ReportRecommendation(
        title=spec.title,
        business_question=question,
        audience=audience,
        decision_supported=rule.rationale,
        required_columns=used,
        metrics=metrics,
        dimensions=dims,
        filters=[],
        prompts=[by_name[d].label for d in dims[:2]],
        drill_path=spec.drill_path or dims,
        chart_type=spec.chart_type,
        tier=option.tier,
        why_this_representation=rule.rationale,
        cautions=rule.avoid_when[:2] + spec.notes,
        confidence="high" if option.tier in ("easy", "medium") else "medium",
        assumptions=["Generated from column roles and cardinality; business definitions still need sign-off."],
    )


HEADLINE_MEASURE_RE = re.compile(r"(revenue|sales|amount|margin|profit|mrr|arr|spend|cost|value|gmv|bookings)")
STAGE_HINT_RE = re.compile(r"(stage|status|step|phase|funnel|state|outcome|disposition)")
NEEDS_CONFIRMATION = {"funnel", "sankey", "cohort_heatmap"}


def rank_measures(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    """Most report-worthy measure first: money, then named headline metrics, then completeness."""

    def key(s: ColumnSemantics) -> tuple:
        total = max(1, s.non_null_count + s.null_count)
        return (
            0 if s.analytical_role == AnalyticalRole.currency else 1,
            0 if HEADLINE_MEASURE_RE.search(s.name.lower()) else 1,
            s.null_count / total,
            s.position,
        )

    return sorted([s for s in semantics if s.analytical_role in NUMERIC_ROLES], key=key)


def rank_dimensions(semantics: list[ColumnSemantics]) -> list[ColumnSemantics]:
    """Dimensions a chart can actually show: small member counts first."""

    def key(s: ColumnSemantics) -> tuple:
        ideal = 0 if 2 <= s.distinct_count <= 12 else 1 if s.distinct_count <= 25 else 2
        return (ideal, s.distinct_count, s.position)

    return sorted(
        [s for s in semantics if s.analytical_role in CATEGORY_ROLES and 2 <= s.distinct_count <= 25],
        key=key,
    )


def heuristic_recommendations(
    semantics: list[ColumnSemantics], overview: DatasetOverview, count: int = 12
) -> list[ReportRecommendation]:
    """Compose recommendations from the chart rules, one per tier-appropriate slot."""
    measures = rank_measures(semantics)
    dims = rank_dimensions(semantics)
    dates = [s for s in semantics if s.analytical_role == AnalyticalRole.datetime_dimension]
    ids = [s for s in semantics if s.analytical_role == AnalyticalRole.identifier]
    rows = overview.analyzed_row_count

    combos: list[tuple[list[str], str, str]] = []
    if measures:
        combos.append(([measures[0].name], "Executives", f"What is our total {measures[0].label}?"))
    if measures and dims:
        combos.append(
            (
                [dims[0].name, measures[0].name],
                "Business analysts",
                f"How does {measures[0].label} compare across {dims[0].label}?",
            )
        )
    if measures and dates:
        combos.append(
            (
                [dates[0].name, measures[0].name],
                "Executives",
                f"How has {measures[0].label} moved over time?",
            )
        )
    if measures and dates and dims:
        combos.append(
            (
                [dates[0].name, measures[0].name, dims[0].name],
                "Regional managers",
                f"Which {dims[0].label} values drive the {measures[0].label} trend?",
            )
        )
    if len(measures) >= 2:
        combos.append(
            (
                [measures[0].name, measures[1].name],
                "Analysts",
                f"Do {measures[0].label} and {measures[1].label} move together?",
            )
        )
    if len(dims) >= 2 and measures:
        combos.append(
            (
                [dims[0].name, dims[1].name, measures[0].name],
                "Operations",
                f"Where does {measures[0].label} concentrate across {dims[0].label} and {dims[1].label}?",
            )
        )
    if len(measures) >= 2 and dims:
        combos.append(
            (
                [measures[0].name, measures[1].name, dims[0].name],
                "Strategy",
                "Which segments emerge when we look at these measures together?",
            )
        )
    if measures and dates and ids:
        combos.append(
            (
                [dates[0].name, ids[0].name, measures[0].name],
                "Customer analytics",
                "How does retention develop after the first period?",
            )
        )
    if measures and dims and dates:
        combos.append(
            (
                [dates[0].name, measures[0].name] + [d.name for d in dims[:2]],
                "Executive committee",
                "What belongs on a single executive page for this data?",
            )
        )

    stage_like = any(STAGE_HINT_RE.search(s.name.lower()) for s in semantics)
    out: list[ReportRecommendation] = []
    seen: set[tuple[str, str]] = set()
    for cols, audience, question in combos:
        selection = build_selection(semantics, cols, rows)
        compatible, _ = evaluate_selection(selection)
        for option in compatible:
            if option.chart_type in NEEDS_CONFIRMATION and not stage_like:
                continue  # needs a confirmed stage/transition column to be meaningful
            key = (option.chart_type, option.spec.title if option.spec else "")
            if key in seen:
                continue
            rec = _recommendation_from_option(option, selection, semantics, audience, question)
            if rec is None:
                continue
            seen.add(key)
            out.append(rec)
        if len(out) >= count * 2:
            break

    tier_order = {"easy": 0, "medium": 1, "complex": 2, "very_complex": 3}
    quota = {"easy": 4, "medium": 4, "complex": 3, "very_complex": 2}
    # Prefer one of each canonical visual per tier over four variants of the same idea.
    preference = {
        "easy": ["kpi_card", "line", "bar", "table", "histogram", "donut"],
        "medium": ["multi_line", "heatmap", "scatter", "pivot_table", "pareto", "box", "stacked_bar", "map"],
        "complex": ["waterfall", "small_multiples", "decomposition_tree", "forecast_line", "cluster_scatter"],
        "very_complex": ["driver_bar", "anomaly_timeline", "scorecard"],
    }
    picked: list[ReportRecommendation] = []
    for tier in sorted(quota, key=lambda t: tier_order[t]):
        in_tier = [r for r in out if r.tier == tier]
        order = preference.get(tier, [])
        in_tier.sort(key=lambda r: order.index(r.chart_type) if r.chart_type in order else len(order))
        chosen: list[ReportRecommendation] = []
        used_types: set[str] = set()
        for rec in in_tier:
            if rec.chart_type in used_types:
                continue
            used_types.add(rec.chart_type)
            chosen.append(rec)
            if len(chosen) >= quota[tier]:
                break
        picked.extend(chosen)
    return picked[:count]


def heuristic_answer(question: str, result: PlanResult | None, anomalies: list[Anomaly]) -> ExecutiveAnswer:
    if result is None or not result.rows:
        return ExecutiveAnswer(
            headline="No rows matched the plan",
            summary="The plan executed but returned no data. Relax the filters or pick a different grain.",
            evidence=[],
            caveats=["Nothing was computed, so no conclusion can be drawn."],
            follow_up_questions=["Should the date range or filters be widened?"],
            evidence_kind=EvidenceKind.fact,
        )

    plan = result.plan
    metric_labels = [m.label or m.column for m in plan.metrics] or ["Row count"]
    first_metric = metric_labels[0]
    rows = result.rows
    values = [r.get(first_metric) for r in rows if isinstance(r.get(first_metric), (int, float))]
    dim = plan.dimensions[0] if plan.dimensions else ("period" if plan.time_dimension else None)

    evidence: list[str] = [f"Executed in {result.executed_ms} ms over {result.row_count:,} returned group(s)."]
    if values:
        total = sum(values)
        evidence.append(f"Total {first_metric}: {total:,.2f}.")
        numeric_rows = [r for r in rows if isinstance(r.get(first_metric), (int, float))]
        if dim and numeric_rows and dim in numeric_rows[0]:
            top = max(numeric_rows, key=lambda r: r[first_metric])
            bottom = min(numeric_rows, key=lambda r: r[first_metric])
            evidence.append(f"Highest {first_metric}: {top.get(dim)} at {top[first_metric]:,.2f}.")
            if len(numeric_rows) > 1:
                evidence.append(f"Lowest: {bottom.get(dim)} at {bottom[first_metric]:,.2f}.")
            if total and len(values) > 1:
                share = 100 * (top[first_metric] / total)
                evidence.append(f"The leading group holds {share:.1f}% of the returned total.")
        if plan.time_dimension and len(values) > 2:
            direction = "higher" if values[-1] > values[0] else "lower" if values[-1] < values[0] else "flat"
            evidence.append(f"The latest period is {direction} than the first period in the returned series.")

    caveats = [
        a.title
        for a in anomalies[:3]
        if not a.columns or any(c in [m.column for m in plan.metrics] + plan.dimensions for c in a.columns)
    ]
    caveats.append("Figures are computed from the uploaded file only; they are not reconciled to a system of record.")
    if result.truncated:
        caveats.append(f"Output was capped at {plan.limit:,} rows.")
    if plan.assumptions:
        caveats.extend(f"Assumption: {a}" for a in plan.assumptions)

    return ExecutiveAnswer(
        headline=f"{first_metric}" + (f" by {dim}" if dim else "") + f" across {result.row_count:,} group(s)",
        summary=(
            f"The question was answered by computing {', '.join(metric_labels)}"
            + (f" grouped by {', '.join(plan.dimensions)}" if plan.dimensions else "")
            + (f" at {plan.time_grain} grain" if plan.time_dimension else "")
            + ". The figures above come from executed queries, not from a language model."
        ),
        evidence=evidence,
        caveats=caveats[:5],
        follow_up_questions=[
            f"Should {first_metric} be compared against a target or prior period?",
            "Which filters does the business normally apply to this metric?",
        ],
        evidence_kind=EvidenceKind.fact,
    )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class AnalystService:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        provider_name: str | None = None,
        dataset_id: str | None = None,
    ) -> None:
        self.requested_provider = (provider_name or "").lower() or None
        self.provider = provider or get_provider(provider_name)
        self.dataset_id = dataset_id

    @property
    def provider_name(self) -> str:
        return self.provider.name

    # ---------------- one gate for every model call ----------------

    def _call(self, prompt: prompts.Prompt, user: str, *, action: str, max_tokens: int) -> dict:
        """Call the provider, record provider-reported usage, allow one repair.

        Usage is recorded for successes and failures alike, because the panel has
        to be able to show a failure state without losing the confirmed total.
        """
        attempts = (user, f"{user}\n\nThe previous reply was not valid JSON. Return only the JSON object.")
        last_error: LLMError | None = None

        for index, body in enumerate(attempts):
            try:
                result = self.provider.complete_json(prompt.system, body, max_tokens=max_tokens)
            except LLMError as exc:
                last_error = exc
                self._record(action, prompt.ref, ok=False, error=str(exc)[:200])
                # Only a malformed-JSON failure is worth one controlled repair.
                if index == 0 and "JSON" in str(exc):
                    continue
                raise
            self._record(
                action,
                prompt.ref,
                usage=result.usage,
                model=result.model,
                request_id=result.request_id,
            )
            return result.data
        raise last_error or LLMError("The provider returned nothing usable.")

    def _record(
        self,
        action: str,
        prompt_ref: str,
        *,
        usage: TokenUsage | None = None,
        model: str | None = None,
        request_id: str | None = None,
        ok: bool = True,
        error: str | None = None,
    ) -> None:
        if not self.dataset_id:
            return
        ledger.record(
            self.dataset_id,
            build_record(
                action=action,
                provider=self.provider.name,
                model=model or self.provider.model,
                prompt_version=prompt_ref,
                usage=usage,
                ok=ok,
                error=error,
                request_id=request_id,
            ),
        )

    # ---------------- recommendations ----------------

    def recommendations(
        self,
        *,
        overview: DatasetOverview,
        columns: list[ColumnProfile],
        semantics: list[ColumnSemantics],
        anomalies: list[Anomaly],
        relationships: list[RelationshipResult],
        business_context: str,
        count: int = 12,
    ) -> RecommendationSet:
        profile = compact_profile(overview, columns, anomalies, relationships)
        notes: list[str] = []
        rejected = 0
        interpretation: BusinessInterpretation | None = None
        recs: list[ReportRecommendation] = []
        prompt = prompts.RECOMMENDATIONS

        if self.provider.name != "heuristic":
            user = prompt.template.format(
                context=prompts.wrap_untrusted(business_context or "(none supplied)"),
                profile=prompts.wrap_untrusted(profile),
                count=count,
                chart_types=", ".join(CHART_TYPES),
            )
            try:
                raw = self._call(prompt, user, action="recommendations", max_tokens=6000)
                interpretation = BusinessInterpretation.model_validate(raw.get("interpretation", {}))
                recs, rejected, notes = self._validate_recommendations(
                    raw.get("recommendations", []), semantics, overview
                )
            except (LLMError, ValueError) as exc:
                notes.append(f"{self.provider.name} output was not usable ({exc}); the built-in narrator was used.")

        if interpretation is None:
            interpretation = heuristic_interpretation(overview, columns, anomalies)
        if not recs:
            recs = heuristic_recommendations(semantics, overview, count)
            if self.provider.name != "heuristic" and not notes:
                notes.append("No model recommendation survived validation; the built-in narrator was used.")

        by_tier: dict[str, list[int]] = {}
        for idx, rec in enumerate(recs):
            by_tier.setdefault(rec.tier, []).append(idx)

        return RecommendationSet(
            dataset_id=overview.dataset_id,
            interpretation=interpretation,
            recommendations=recs,
            by_tier=by_tier,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=prompt.ref,
            profile_version=profile["profile_version"],
            generated_at=datetime.now(UTC),
            rejected_count=rejected,
            rejection_notes=notes,
        )

    def _validate_recommendations(
        self, raw: list[dict], semantics: list[ColumnSemantics], overview: DatasetOverview
    ) -> tuple[list[ReportRecommendation], int, list[str]]:
        by_name = {s.name: s for s in semantics}
        lower = {s.name.lower(): s.name for s in semantics}
        good: list[ReportRecommendation] = []
        notes: list[str] = []
        rejected = 0

        for item in raw:
            try:
                rec = ReportRecommendation.model_validate(item)
            except ValueError as exc:
                rejected += 1
                notes.append(f"Rejected a recommendation that did not match the schema: {str(exc)[:120]}")
                continue

            repairs: list[str] = []
            resolved: list[str] = []
            for col in rec.required_columns:
                if col in by_name:
                    resolved.append(col)
                elif col.lower() in lower:
                    resolved.append(lower[col.lower()])
                    repairs.append(f"Matched '{col}' to '{lower[col.lower()]}'.")
                else:
                    repairs.append(f"Removed non-existent column '{col}'.")
            if not resolved:
                rejected += 1
                notes.append(f"Rejected '{rec.title}': none of its columns exist in the dataset.")
                continue

            metrics: list[MetricSpec] = []
            for m in rec.metrics:
                name = m.column if m.column in by_name else lower.get(m.column.lower())
                if not name:
                    repairs.append(f"Removed metric on unknown column '{m.column}'.")
                    continue
                sem = by_name[name]
                agg = m.aggregation
                if sem.analytical_role not in NUMERIC_ROLES and agg in (
                    Aggregation.sum,
                    Aggregation.avg,
                    Aggregation.median,
                ):
                    agg = Aggregation.count_distinct
                    repairs.append(f"Changed the aggregation on '{sem.label}' to count distinct for its role.")
                if sem.analytical_role == AnalyticalRole.percentage and agg == Aggregation.sum:
                    agg = Aggregation.avg
                    repairs.append(f"Changed sum to average on the rate column '{sem.label}'.")
                metrics.append(MetricSpec(column=name, aggregation=agg, label=m.label or sem.label))

            rule = RULES_BY_TYPE.get(rec.chart_type)
            if rule is None:
                rejected += 1
                notes.append(f"Rejected '{rec.title}': unsupported chart type '{rec.chart_type}'.")
                continue

            selection = build_selection(semantics, resolved, overview.analyzed_row_count)
            blockers = rule.check(selection)
            if overview.analyzed_row_count < rule.min_rows:
                blockers.append(f"Needs at least {rule.min_rows} rows.")
            if blockers:
                rejected += 1
                notes.append(f"Rejected '{rec.title}': {blockers[0]}")
                continue
            if rec.tier != rule.tier:
                repairs.append(f"Moved to the '{rule.tier}' tier to match the chart's real complexity.")

            good.append(
                rec.model_copy(
                    update={
                        "required_columns": resolved,
                        "metrics": metrics,
                        "dimensions": [d if d in by_name else lower.get(d.lower(), "") for d in rec.dimensions],
                        "drill_path": [d for d in rec.drill_path if d in by_name or d.lower() in lower],
                        "tier": rule.tier,
                        "repairs": repairs,
                    }
                )
            )
        return good, rejected, notes

    # ---------------- chart ranking ----------------

    def rank_charts(
        self,
        *,
        selection: Selection,
        options: list[ChartOption],
        semantics: list[ColumnSemantics],
        question: str | None,
    ) -> tuple[list[ChartOption], bool, str | None]:
        if self.provider.name == "heuristic" or not options:
            return options, False, None

        payload = [{"chart_type": o.chart_type, "tier": o.tier, "prerequisites": o.prerequisites} for o in options]
        prompt = prompts.CHART_RANKING
        user = prompt.template.format(
            selection=prompts.wrap_untrusted(
                [
                    {"name": c.name, "label": c.label, "role": c.analytical_role.value, "distinct": c.distinct_count}
                    for c in selection.columns
                ]
            ),
            question=prompts.wrap_untrusted(question or "(none)"),
            options=json.dumps(payload),
        )
        try:
            raw = self._call(prompt, user, action="chart_ranking", max_tokens=2000)
        except LLMError as exc:
            return options, False, f"Ranking fell back to rule order ({exc})."

        by_type = {o.chart_type: o for o in options}
        ranked: list[ChartOption] = []
        for entry in raw.get("ranking", []):
            option = by_type.pop(entry.get("chart_type"), None)
            if option is None:
                continue
            rationale = str(entry.get("rationale") or option.rationale)[:400]
            caution = str(entry.get("caution") or "")[:200]
            ranked.append(
                option.model_copy(
                    update={
                        "rank": len(ranked) + 1,
                        "rationale": rationale,
                        "avoid_when": ([caution] if caution else []) + option.avoid_when,
                    }
                )
            )
        for leftover in by_type.values():
            ranked.append(leftover.model_copy(update={"rank": len(ranked) + 1}))
        return ranked, True, None

    # ---------------- question answering ----------------

    def plan_for_question(
        self, question: str, semantics: list[ColumnSemantics], selected: list[str]
    ) -> tuple[AnalysisPlan, bool, str | None]:
        from ..core.query_plan import heuristic_plan

        if self.provider.name == "heuristic":
            return heuristic_plan(question, semantics, selected), False, None

        prompt = prompts.ANALYSIS_PLAN
        user = prompt.template.format(
            columns=prompts.wrap_untrusted(_columns_for_prompt(semantics)),
            selected=", ".join(selected) or "(none)",
            question=prompts.wrap_untrusted(question),
        )
        try:
            raw = self._call(prompt, user, action="analysis_plan", max_tokens=1500)
            return AnalysisPlan.model_validate(raw), True, None
        except (LLMError, ValueError) as exc:
            return (
                heuristic_plan(question, semantics, selected),
                False,
                f"The model plan was unusable ({str(exc)[:140]}); a rule-based plan was used.",
            )

    def answer(
        self,
        *,
        question: str,
        result: PlanResult | None,
        anomalies: list[Anomaly],
    ) -> tuple[ExecutiveAnswer, bool, str]:
        prompt = prompts.EXECUTIVE_ANSWER
        if self.provider.name == "heuristic" or result is None:
            return heuristic_answer(question, result, anomalies), False, prompt.ref

        user = prompt.template.format(
            question=prompts.wrap_untrusted(question),
            plan=json.dumps(result.plan.model_dump(mode="json"), default=str),
            rows=json.dumps(result.rows[:60], default=str),
            quality=json.dumps([a.title for a in anomalies[:8]]),
        )
        try:
            raw = self._call(prompt, user, action="executive_answer", max_tokens=1800)
            answer = ExecutiveAnswer.model_validate(raw)
            answer.evidence_kind = EvidenceKind.inference
            answer.caveats.append("Numbers were computed in the backend; the wording is model-generated.")
            return answer, True, prompt.ref
        except (LLMError, ValueError):
            return heuristic_answer(question, result, anomalies), False, prompt.ref

    # ---------------- dataset summary ----------------

    def business_summary(
        self,
        *,
        overview: DatasetOverview,
        columns: list[ColumnProfile],
        anomalies: list[Anomaly],
        business_context: str,
    ) -> tuple[BusinessInterpretation, bool]:
        if self.provider.name == "heuristic":
            return heuristic_interpretation(overview, columns, anomalies), False
        prompt = prompts.BUSINESS_SUMMARY
        user = prompt.template.format(
            context=prompts.wrap_untrusted(business_context or "(none supplied)"),
            profile=prompts.wrap_untrusted(compact_profile(overview, columns, anomalies, [])),
        )
        try:
            raw = self._call(prompt, user, action="dataset_summary", max_tokens=1200)
            return BusinessInterpretation.model_validate(raw), True
        except (LLMError, ValueError):
            return heuristic_interpretation(overview, columns, anomalies), False
