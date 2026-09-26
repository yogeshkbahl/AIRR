from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.anomalies import affected_rows_preview, detect_anomalies
from app.core.chart_rules import (
    build_selection,
    evaluate_selection,
    repair_chart_spec,
    validate_chart_spec,
)
from app.core.query_plan import PlanError, execute_plan, heuristic_plan
from app.core.relationships import analyse_pair, build_matrix, correlation_ratio, cramers_v, pair_detail
from app.core.semantics import classify_column
from app.llm.service import AnalystService, compact_profile
from app.schemas import (
    Aggregation,
    AnalysisPlan,
    ChartEncoding,
    ChartSpec,
    MetricSpec,
    QueryFilter,
)


def semantics_for(df: pd.DataFrame):
    return [classify_column(df[c], c, i, len(df)) for i, c in enumerate(df.columns)]


# --------------------------------------------------------------------------- #
# Anomalies
# --------------------------------------------------------------------------- #


def test_expected_anomaly_kinds_are_found(session_obj):
    kinds = {a.kind for a in session_obj.anomalies}
    for expected in {
        "duplicate_rows",
        "missing_values",
        "constant_column",
        "numeric_outliers",
        "inconsistent_casing",
        "future_dates",
        "suspicious_values",
    }:
        assert expected in kinds, f"{expected} was not detected"


def test_every_anomaly_explains_itself(session_obj):
    for anomaly in session_obj.anomalies:
        assert anomaly.method
        assert anomaly.explanation
        assert anomaly.recommended_action
        assert anomaly.severity.value in {"info", "low", "medium", "high"}
        assert anomaly.affected_rows >= 0
        assert 0 <= anomaly.affected_percent <= 100


def test_business_rule_findings_are_labelled_as_hypotheses(session_obj):
    negatives = next(a for a in session_obj.anomalies if a.kind == "suspicious_values")
    assert negatives.evidence_kind.value == "assumption"


def test_anomaly_preview_hides_sensitive_columns(session_obj):
    anomaly = next(a for a in session_obj.anomalies if a.row_filter and a.row_filter.get("type") == "duplicate_rows")
    preview = affected_rows_preview(session_obj.df, anomaly.row_filter, {"customer_email"}, 10)
    assert "customer_email" not in preview.columns
    assert len(preview) > 0


def test_mixed_type_column_is_flagged():
    df = pd.DataFrame({"amount_text": ["10", "20", "30", "N/A", "40", "TBD"] * 20})
    found = detect_anomalies(df, semantics_for(df))
    assert any(a.kind == "mixed_types" for a in found)


# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


def test_method_follows_the_pair_of_roles(session_obj):
    by_name = {s.name: s for s in session_obj.semantics}
    df = session_obj.df

    numeric = analyse_pair(df, by_name["units_sold"], by_name["revenue_amount"])
    assert numeric.method == "pearson"
    assert numeric.secondary_method == "spearman"

    categorical = analyse_pair(df, by_name["region"], by_name["product_category"])
    assert categorical.method == "cramers_v"

    mixed = analyse_pair(df, by_name["region"], by_name["revenue_amount"])
    assert mixed.method == "correlation_ratio_eta"

    timed = analyse_pair(df, by_name["order_date"], by_name["revenue_amount"])
    assert timed.method == "spearman_vs_time"


def test_identifiers_and_text_are_not_correlated(session_obj):
    by_name = {s.name: s for s in session_obj.semantics}
    result = analyse_pair(session_obj.df, by_name["order_id"], by_name["revenue_amount"])
    assert result.pair_kind == "unsupported"
    assert not result.reliable


def test_small_samples_are_suppressed():
    df = pd.DataFrame({"revenue_amount": [1.0, 2.0, 3.5], "units_sold": [1, 2, 3]})
    result = analyse_pair(df, *semantics_for(df)[:2])
    assert not result.reliable
    assert "complete pairs" in " ".join(result.warnings)


def test_cramers_v_and_eta_ranges():
    a = pd.Series(["x", "y"] * 100)
    v, p, n = cramers_v(a, a)
    assert v is not None and 0.9 <= v <= 1.0 and n == 200

    eta, _, _ = correlation_ratio(pd.Series(["a"] * 50 + ["b"] * 50), pd.Series([1.0] * 50 + [9.0] * 50))
    assert eta is not None and eta > 0.9


def test_correlation_interpretation_warns_about_causation(session_obj):
    by_name = {s.name: s for s in session_obj.semantics}
    result = analyse_pair(session_obj.df, by_name["units_sold"], by_name["revenue_amount"])
    assert "not causation" in result.interpretation


def test_matrix_is_symmetric_and_drillable(session_obj):
    matrix = build_matrix(session_obj.df, session_obj.semantics)
    size = len(matrix.columns)
    for i in range(size):
        assert matrix.values[i][i] == 1.0
        for j in range(size):
            assert matrix.values[i][j] == matrix.values[j][i]

    detail = pair_detail(
        session_obj.df, session_obj.semantics, "region", "revenue_amount", segment_by="product_category"
    )
    assert detail.chart.chart_type == "box"
    assert detail.result.sample_size > 0


def test_simpsons_paradox_is_detected():
    rng = np.random.default_rng(3)
    frames = []
    for index, group in enumerate(["A", "B", "C"]):
        x = rng.normal(10 * index, 1, 120)
        y = -2.0 * x + 25 * index + rng.normal(0, 0.5, 120)
        frames.append(pd.DataFrame({"spend_amount": x, "revenue_amount": y, "segment_name": group}))
    df = pd.concat(frames, ignore_index=True)
    sem = {s.name: s for s in semantics_for(df)}
    detail = pair_detail(df, list(sem.values()), "spend_amount", "revenue_amount", segment_by="segment_name")
    assert detail.segment is not None
    assert detail.segment.simpsons_paradox_suspected


# --------------------------------------------------------------------------- #
# Chart rules
# --------------------------------------------------------------------------- #


def test_only_compatible_charts_are_offered(session_obj):
    selection = build_selection(session_obj.semantics, ["region", "revenue_amount"], 240)
    compatible, rejected = evaluate_selection(selection)
    types = {o.chart_type for o in compatible}
    assert {"kpi_card", "bar", "table"} <= types
    assert "scatter" not in types  # needs two measures
    assert "line" not in types  # no date selected
    assert all(o.blockers for o in rejected)
    assert all(o.spec is not None for o in compatible)
    assert [o.rank for o in compatible] == list(range(1, len(compatible) + 1))


def test_part_to_whole_needs_few_categories():
    df = pd.DataFrame(
        {
            "customer_segment": [f"Segment {i % 30}" for i in range(300)],
            "revenue_amount": np.linspace(1, 100, 300),
        }
    )
    compatible, rejected = evaluate_selection(build_selection(semantics_for(df), list(df.columns), 300))
    assert "donut" not in {o.chart_type for o in compatible}
    assert any(o.chart_type == "donut" and o.blockers for o in rejected)


def test_complex_charts_need_enough_rows(session_obj):
    selection = build_selection(session_obj.semantics, ["order_date", "order_id", "revenue_amount"], 40)
    compatible, rejected = evaluate_selection(selection)
    assert "cohort_heatmap" not in {o.chart_type for o in compatible}
    assert any("rows" in b for o in rejected if o.chart_type == "cohort_heatmap" for b in o.blockers)


def test_spec_validation_rejects_invented_columns(session_obj):
    spec = ChartSpec(
        chart_type="bar",
        title="Invented",
        encoding=ChartEncoding(x="not_a_column", y="revenue_amount"),
        aggregation=Aggregation.sum,
    )
    ok, problems = validate_chart_spec(spec, session_obj.semantics, 240)
    assert not ok
    assert "does not exist" in problems[0]


def test_spec_repair_fixes_case_and_rate_aggregation(session_obj):
    spec = ChartSpec(
        chart_type="bar",
        title="Discount by region",
        encoding=ChartEncoding(x="REGION", y="discount_pct"),
        aggregation=Aggregation.sum,
    )
    repaired, notes = repair_chart_spec(spec, session_obj.semantics, 240)
    assert repaired is not None
    assert repaired.encoding.x == "region"
    assert repaired.aggregation == Aggregation.avg
    assert any("rate" in n for n in notes)


def test_summing_an_identifier_is_invalid(session_obj):
    spec = ChartSpec(
        chart_type="bar",
        title="Sum of ids",
        encoding=ChartEncoding(x="region", y="customer_id"),
        aggregation=Aggregation.sum,
    )
    ok, problems = validate_chart_spec(spec, session_obj.semantics, 240)
    assert not ok
    assert any("not a valid aggregation" in p for p in problems)


# --------------------------------------------------------------------------- #
# Governed query plan
# --------------------------------------------------------------------------- #


def test_plan_executes_and_reports_sql(session_obj):
    plan = AnalysisPlan(
        intent="revenue by region",
        metrics=[MetricSpec(column="revenue_amount", aggregation=Aggregation.sum, label="Revenue")],
        dimensions=["region"],
        limit=10,
    )
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    assert result.row_count > 0
    assert "GROUP BY" in result.sql_like
    assert set(result.columns) == {"region", "Revenue"}
    assert sum(r["Revenue"] for r in result.rows) > 0


def test_plan_rejects_unknown_columns(session_obj):
    plan = AnalysisPlan(
        intent="bad",
        metrics=[MetricSpec(column="revenue_amount", aggregation=Aggregation.sum)],
        dimensions=["'; DROP TABLE dataset; --"],
    )
    with pytest.raises(PlanError):
        execute_plan(session_obj.df, plan, session_obj.semantics)


def test_filter_values_are_parameterized(session_obj):
    plan = AnalysisPlan(
        intent="filtered",
        metrics=[MetricSpec(column="revenue_amount", aggregation=Aggregation.sum, label="Revenue")],
        dimensions=["region"],
        filters=[QueryFilter(column="region", op="eq", value="North' OR 1=1 --")],
    )
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    assert result.row_count == 0  # the injection string is treated as a literal


def test_rate_columns_are_never_summed(session_obj):
    plan = AnalysisPlan(
        intent="rates",
        metrics=[MetricSpec(column="discount_pct", aggregation=Aggregation.sum, label="Discount")],
        dimensions=["region"],
    )
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    assert result.plan.metrics[0].aggregation == Aggregation.avg


def test_row_limit_is_enforced(session_obj):
    plan = AnalysisPlan(
        intent="limited",
        metrics=[MetricSpec(column="revenue_amount", aggregation=Aggregation.sum, label="Revenue")],
        dimensions=["order_id"],
        limit=5,
    )
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    assert result.row_count == 5
    assert result.truncated


def test_time_series_is_ordered_oldest_first(session_obj):
    plan = heuristic_plan("monthly revenue trend", session_obj.semantics)
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    periods = [r["period"] for r in result.rows]
    assert periods == sorted(periods)


def test_heuristic_plan_finds_named_columns(session_obj):
    plan = heuristic_plan("top 3 product categories by revenue", session_obj.semantics)
    assert plan.dimensions == ["product_category"]
    assert plan.metrics[0].column == "revenue_amount"
    assert plan.limit == 3


# --------------------------------------------------------------------------- #
# LLM contract
# --------------------------------------------------------------------------- #


def test_compact_profile_excludes_raw_and_sensitive_values(session_obj):
    payload = compact_profile(
        session_obj.overview, session_obj.columns, session_obj.anomalies, session_obj.relationships
    )
    text = str(payload)
    assert "@example.com" not in text
    assert "sample_values" not in text
    email = next(c for c in payload["columns"] if c["name"] == "customer_email")
    assert email.get("sensitive") is True
    assert "top_values" not in email


def test_recommendations_cover_tiers_and_real_columns(session_obj):
    service = AnalystService(provider_name="heuristic")
    result = service.recommendations(
        overview=session_obj.overview,
        columns=session_obj.columns,
        semantics=session_obj.semantics,
        anomalies=session_obj.anomalies,
        relationships=session_obj.relationships,
        business_context="Retail revenue review",
    )
    names = {c.name for c in session_obj.columns}
    assert len(result.recommendations) >= 6
    assert {"easy", "medium"} <= set(result.by_tier)
    for rec in result.recommendations:
        assert set(rec.required_columns) <= names
        for metric in rec.metrics:
            assert metric.column in names
            assert metric.aggregation != Aggregation.none


def test_model_output_with_fake_columns_is_rejected(session_obj):
    service = AnalystService(provider_name="heuristic")
    raw = [
        {
            "title": "Ghost report",
            "business_question": "?",
            "audience": "Execs",
            "decision_supported": "none",
            "required_columns": ["imaginary_column"],
            "metrics": [{"column": "imaginary_column", "aggregation": "sum"}],
            "chart_type": "bar",
            "tier": "easy",
            "why_this_representation": "n/a",
        },
        {
            "title": "Revenue by region",
            "business_question": "Where does revenue come from?",
            "audience": "Execs",
            "decision_supported": "Focus",
            "required_columns": ["region", "REVENUE_AMOUNT"],
            "metrics": [{"column": "customer_id", "aggregation": "sum"}],
            "dimensions": ["region"],
            "chart_type": "bar",
            "tier": "very_complex",
            "why_this_representation": "Ranked comparison",
        },
    ]
    kept, rejected, notes = service._validate_recommendations(raw, session_obj.semantics, session_obj.overview)
    assert rejected == 1
    assert len(kept) == 1
    assert kept[0].required_columns == ["region", "revenue_amount"]
    assert kept[0].metrics[0].aggregation == Aggregation.count_distinct  # identifier cannot be summed
    assert kept[0].tier == "easy"  # corrected to the chart's real complexity
    assert notes


def test_answers_are_built_from_executed_results(session_obj):
    service = AnalystService(provider_name="heuristic")
    plan = heuristic_plan("revenue by region", session_obj.semantics)
    result = execute_plan(session_obj.df, plan, session_obj.semantics)
    answer, llm_used, prompt_ref = service.answer(
        question="revenue by region", result=result, anomalies=session_obj.anomalies
    )
    assert not llm_used
    assert prompt_ref.startswith("executive_answer@")
    assert answer.evidence_kind.value == "fact"
    assert any("Total" in e for e in answer.evidence)
    assert answer.caveats
