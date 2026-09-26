"""Governed data-quality rules and dataset preview."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.preview import build_preview
from app.core.quality_rules import (
    RuleError,
    evaluate_rule,
    evaluate_rules,
    merge_rules,
    report_csv,
    suggest_rules,
    validate_rules,
)
from app.core.query_plan import PlanError
from app.core.semantics import classify_column
from app.schemas import PreviewRequest, QualityRule, Severity


def semantics_for(df: pd.DataFrame):
    return [classify_column(df[c], c, i, len(df)) for i, c in enumerate(df.columns)]


@pytest.fixture()
def rules_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["A1", "A2", "A3", "A3", "A5", "A6"],
            "region": ["North", "South", "north ", "East", None, "Atlantis"],
            "revenue_amount": [10.0, 20.0, -5.0, 40.0, 50.0, 60.0],
            "satisfaction_score": [4.0, 5.0, 9.0, 3.0, 4.5, 2.0],
            "customer_email": ["a@b.com", "not-an-email", "c@d.org", "e@f.net", "g@h.io", "i@j.co"],
            "order_date": pd.to_datetime(
                ["2024-01-01", "2024-02-01", "2099-01-01", "2024-03-01", "2024-04-01", "2024-05-01"]
            ),
            "notes": ["", "ok", "ok", "ok", "ok", "ok"],
        }
    )


def rule(**kwargs) -> QualityRule:
    defaults = {"id": "r1", "column": "region", "kind": "required", "enabled": True}
    return QualityRule(**{**defaults, **kwargs})


# --------------------------------------------------------------------------- #
# Rule evaluation
# --------------------------------------------------------------------------- #


def test_required_rule_counts_nulls_and_blank_strings(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    region = evaluate_rule(rules_frame, rule(column="region", kind="required"), sem["region"])
    assert region.failed_rows == 1
    assert region.passed is False
    assert "IS NOT NULL" in region.expression

    notes = evaluate_rule(rules_frame, rule(column="notes", kind="required"), sem["notes"])
    # A blank string is a missing value for a required rule, unlike in the
    # profile where nulls and blanks are counted separately.
    assert notes.failed_rows == 1


def test_unique_rule_finds_a_repeated_key(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(rules_frame, rule(column="order_id", kind="unique"), sem["order_id"])
    assert result.failed_rows == 2  # both rows of the duplicated pair
    assert result.passed is False
    assert "COUNT(DISTINCT" in result.expression


def test_accepted_values_rule_ignores_case_and_whitespace(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(
        rules_frame,
        rule(column="region", kind="accepted_values", allowed=["North", "South", "East"]),
        sem["region"],
    )
    # 'north ' is a casing/whitespace variant, which is a consistency anomaly
    # rather than a breach; only 'Atlantis' is genuinely not a member.
    assert result.failed_rows == 1
    assert result.sample_values == ["Atlantis"]
    assert "case-insensitive" in result.expression


def test_numeric_range_rule_respects_inclusivity(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    inclusive = evaluate_rule(
        rules_frame,
        rule(column="revenue_amount", kind="numeric_range", min_value=0.0),
        sem["revenue_amount"],
    )
    assert inclusive.failed_rows == 1  # the -5 row

    exclusive = evaluate_rule(
        rules_frame,
        rule(column="revenue_amount", kind="numeric_range", min_value=10.0, inclusive=False),
        sem["revenue_amount"],
    )
    assert exclusive.failed_rows == 2  # -5 and the boundary value 10


def test_scale_rule_catches_an_out_of_scale_score(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(
        rules_frame,
        rule(column="satisfaction_score", kind="numeric_range", min_value=1, max_value=5, severity=Severity.high),
        sem["satisfaction_score"],
    )
    assert result.failed_rows == 1
    assert result.severity == Severity.high
    assert result.evidence_kind.value == "fact"


def test_regex_rule_flags_a_malformed_value(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(
        rules_frame,
        rule(column="customer_email", kind="regex_format", pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
        sem["customer_email"],
    )
    assert result.failed_rows == 1
    # The column looks like personal data, so the example is masked.
    assert all("not-an-email" not in value for value in result.sample_values)


def test_date_rule_can_forbid_future_dates(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(
        rules_frame, rule(column="order_date", kind="date_range", allow_future=False), sem["order_date"]
    )
    assert result.failed_rows == 1
    assert "today" in result.expression


def test_date_rule_can_bound_a_window(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    result = evaluate_rule(
        rules_frame,
        rule(column="order_date", kind="date_range", earliest="2024-02-01", latest="2024-04-30"),
        sem["order_date"],
    )
    assert result.failed_rows == 3  # January, the 2099 row and May


def test_tolerance_lets_a_known_level_of_failure_pass(rules_frame):
    sem = {s.name: s for s in semantics_for(rules_frame)}
    strict = evaluate_rule(rules_frame, rule(column="region", kind="required"), sem["region"])
    lenient = evaluate_rule(rules_frame, rule(column="region", kind="required", max_fail_percent=25.0), sem["region"])
    assert strict.passed is False
    assert lenient.passed is True
    assert lenient.failed_rows == strict.failed_rows  # the count is unchanged


# --------------------------------------------------------------------------- #
# Rule sets
# --------------------------------------------------------------------------- #


def test_validation_rejects_unusable_rules(rules_frame):
    semantics = semantics_for(rules_frame)
    with pytest.raises(RuleError):
        validate_rules([rule(column="region", kind="accepted_values", allowed=[])], semantics)
    with pytest.raises(RuleError):
        validate_rules([rule(column="region", kind="regex_format", pattern="([")], semantics)
    with pytest.raises(RuleError):
        validate_rules([rule(column="revenue_amount", kind="numeric_range")], semantics)
    with pytest.raises(RuleError):
        validate_rules([rule(column="region", kind="required", max_fail_percent=150)], semantics)


def test_validation_drops_rules_for_columns_that_no_longer_exist(rules_frame):
    kept, dropped = validate_rules([rule(column="region"), rule(id="r2", column="cost")], semantics_for(rules_frame))
    assert [r.column for r in kept] == ["region"]
    assert dropped == ["cost"]


def test_suggestions_are_proposals_not_active_rules(rules_frame):
    from app.core.profiling import build_column_profile

    semantics = semantics_for(rules_frame)
    profiles = [build_column_profile(rules_frame, sem, len(rules_frame)) for sem in semantics]
    suggested = suggest_rules(semantics, profiles)

    assert suggested, "a profiled dataset should produce at least one proposal"
    assert all(r.enabled is False for r in suggested)
    assert all(r.origin == "suggested" for r in suggested)
    assert all(r.description and r.remediation for r in suggested)
    # Nothing is proposed for a column that is already incomplete.
    assert not any(r.column == "region" and r.kind == "required" for r in suggested)


def test_merge_keeps_untouched_suggestions_and_takes_user_edits():
    existing = [rule(id="a", column="region"), rule(id="b", column="revenue_amount", kind="unique")]
    incoming = [rule(id="a", column="region", severity=Severity.high, enabled=True)]
    merged = merge_rules(existing, incoming)
    assert len(merged) == 2
    assert next(r for r in merged if r.id == "a").severity == Severity.high


def test_report_separates_breaches_from_suspicions(rules_frame):
    from app.core.anomalies import detect_anomalies

    semantics = semantics_for(rules_frame)
    anomalies = detect_anomalies(rules_frame, semantics)
    report = evaluate_rules(
        rules_frame,
        [
            rule(id="a", column="region", kind="required"),
            rule(id="b", column="order_id", kind="unique"),
            rule(id="c", column="revenue_amount", kind="numeric_range", min_value=0.0),
            rule(id="d", column="notes", kind="required", enabled=False),
        ],
        semantics,
        dataset_id="ds1",
        sampled=False,
        anomalies=anomalies,
    )
    assert report.rules_evaluated == 3  # the disabled rule is not run
    assert report.rules_failed == 3
    assert all(result.evidence_kind.value == "fact" for result in report.results)
    assert report.suspicions, "statistical findings are reported separately"
    assert "statistical suspicions" in report.note


def test_csv_export_is_safe(rules_frame):
    semantics = semantics_for(rules_frame)
    report = evaluate_rules(
        rules_frame,
        [rule(id="a", column="customer_email", kind="regex_format", pattern=r"^\S+@\S+\.\S+$")],
        semantics,
        dataset_id="ds1",
        sampled=False,
    )
    csv_text = report_csv(report, filename="=cmd|'/c calc'!A1.csv")
    assert csv_text.startswith("AI BI Analyst data-quality report")
    assert "'=cmd" in csv_text  # formula neutralised
    assert "not-an-email" not in csv_text  # failing values are not exported
    assert "regex_format" in csv_text


# --------------------------------------------------------------------------- #
# Dataset preview
# --------------------------------------------------------------------------- #


def test_preview_paginates_and_reports_totals(session_obj):
    first = build_preview(session_obj.df, session_obj.semantics, PreviewRequest(offset=0, limit=10), dataset_id="ds1")
    second = build_preview(session_obj.df, session_obj.semantics, PreviewRequest(offset=10, limit=10), dataset_id="ds1")
    assert first.returned_rows == second.returned_rows == 10
    assert first.total_rows == len(session_obj.df)
    assert first.rows[0] != second.rows[0]


def test_preview_masks_sensitive_columns(session_obj):
    result = build_preview(session_obj.df, session_obj.semantics, PreviewRequest(limit=5), dataset_id="ds1")
    assert "customer_email" in result.masked_columns
    for row in result.rows:
        assert "@example.com" not in str(row["customer_email"]) or "*" in str(row["customer_email"])
    assert "masked" in result.note


def test_preview_sorts_by_a_real_column(session_obj):
    result = build_preview(
        session_obj.df,
        session_obj.semantics,
        PreviewRequest(limit=5, sort_by="revenue_amount", sort_desc=True),
        dataset_id="ds1",
    )
    values = [row["revenue_amount"] for row in result.rows if row["revenue_amount"] is not None]
    assert values == sorted(values, reverse=True)


def test_preview_filters_with_an_allow_listed_operator(session_obj):
    result = build_preview(
        session_obj.df,
        session_obj.semantics,
        PreviewRequest(limit=50, filter_column="region", filter_op="eq", filter_value="North"),
        dataset_id="ds1",
    )
    assert result.filtered_rows < result.total_rows
    assert all("north" in str(row["region"]).lower() for row in result.rows)


def test_preview_rejects_an_unknown_column(session_obj):
    with pytest.raises(PlanError):
        build_preview(
            session_obj.df,
            session_obj.semantics,
            PreviewRequest(filter_column="ghost", filter_op="eq", filter_value=1),
            dataset_id="ds1",
        )
    with pytest.raises(PlanError):
        build_preview(session_obj.df, session_obj.semantics, PreviewRequest(sort_by="ghost"), dataset_id="ds1")


def test_preview_refuses_to_filter_on_personal_data(session_obj):
    with pytest.raises(PlanError):
        build_preview(
            session_obj.df,
            session_obj.semantics,
            PreviewRequest(filter_column="customer_email", filter_op="eq", filter_value="a@b.com"),
            dataset_id="ds1",
        )


def test_preview_filter_value_is_never_executed(session_obj):
    # The value is used as data by pandas, so an injection attempt matches nothing.
    result = build_preview(
        session_obj.df,
        session_obj.semantics,
        PreviewRequest(limit=10, filter_column="region", filter_op="eq", filter_value="North'; DROP TABLE dataset; --"),
        dataset_id="ds1",
    )
    assert result.filtered_rows == 0


def test_preview_limit_is_capped(session_obj):
    result = build_preview(session_obj.df, session_obj.semantics, PreviewRequest(limit=200), dataset_id="ds1")
    assert result.limit <= 200
    assert result.returned_rows <= 200


def test_preview_handles_an_empty_page(session_obj):
    result = build_preview(
        session_obj.df, session_obj.semantics, PreviewRequest(offset=10_000, limit=10), dataset_id="ds1"
    )
    assert result.returned_rows == 0
    assert result.rows == []


def test_preview_serialises_timestamps_and_nan(session_obj):
    result = build_preview(session_obj.df, session_obj.semantics, PreviewRequest(limit=20), dataset_id="ds1")
    for row in result.rows:
        for value in row.values():
            assert not isinstance(value, (pd.Timestamp, np.generic))
