from __future__ import annotations

import pandas as pd
import pytest

from app.core.ingestion import (
    IngestionError,
    detect_delimiter,
    load_dataframe,
    neutralize_csv_value,
    sanitize_filename,
    sniff_format,
)
from app.core.semantics import classify_column
from app.core.sensitive import classify_sensitive, mask_value
from app.schemas import Aggregation, AnalyticalRole, PhysicalType

# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def test_filenames_are_sanitized():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("my report (final).csv") == "my_report_final_.csv"
    assert sanitize_filename("") == "upload"


def test_content_is_trusted_over_extension():
    assert sniff_format(b"PAR1\x00\x00", "data.csv") == "parquet"
    assert sniff_format(b"PK\x03\x04", "data.csv") == "xlsx"
    with pytest.raises(IngestionError):
        sniff_format(b"%PDF-1.7", "data.csv")
    with pytest.raises(IngestionError):
        sniff_format(b"col_a,col_b\n1,2", "data.parquet")


def test_delimiter_detection():
    assert detect_delimiter("a;b;c\n1;2;3", "f.csv")[0] == ";"
    assert detect_delimiter("a\tb\n1\t2", "f.tsv")[0] == "\t"
    assert detect_delimiter("a,b\n1,2", "f.csv")[0] == ","


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(IngestionError):
        load_dataframe(path, "empty.csv")


def test_header_only_file_is_rejected(tmp_path):
    path = tmp_path / "head.csv"
    path.write_text("a,b,c\n")
    with pytest.raises(IngestionError):
        load_dataframe(path, "head.csv")


def test_csv_loads_with_detected_settings(csv_path):
    df, info = load_dataframe(csv_path, "orders.csv")
    assert info.detected_format == "text"
    assert info.delimiter == ","
    assert info.total_rows == len(df)
    assert df.shape[1] == 13


def test_formula_injection_is_neutralized():
    assert neutralize_csv_value("=SUM(A1:A9)").startswith("'=")
    assert neutralize_csv_value("+1") == "'+1"
    assert neutralize_csv_value("North") == "North"
    assert neutralize_csv_value(42) == 42


# --------------------------------------------------------------------------- #
# Semantics
# --------------------------------------------------------------------------- #


def classify(series: pd.Series, name: str):
    return classify_column(series, name, position=0, total_rows=len(series))


def test_postal_code_is_not_a_measure():
    sem = classify(pd.Series([94105, 10001, 60601, 30301] * 20), "postal_code")
    assert sem.analytical_role != AnalyticalRole.measure
    assert sem.default_aggregation != Aggregation.sum


def test_foreign_key_is_not_a_measure():
    sem = classify(pd.Series([1, 2, 3, 4, 5] * 20), "customer_id")
    assert sem.analytical_role == AnalyticalRole.identifier
    assert sem.default_aggregation == Aggregation.count_distinct


def test_currency_and_rate_defaults():
    revenue = classify(pd.Series([10.5, 22.25, 31.0] * 20), "revenue_amount")
    assert revenue.analytical_role == AnalyticalRole.currency
    assert revenue.default_aggregation == Aggregation.sum

    rate = classify(pd.Series([12.5, 8.0, 30.0] * 20), "discount_pct")
    assert rate.analytical_role == AnalyticalRole.percentage
    assert rate.default_aggregation == Aggregation.avg

    unit = classify(pd.Series([4.5, 6.25, 9.0] * 20), "unit_price")
    assert unit.default_aggregation == Aggregation.avg


def test_averages_and_counts_named_as_such_get_the_right_aggregation():
    avg_value = classify(pd.Series([12.5, 80.0, 31.0] * 20), "avg_transaction_value")
    assert avg_value.default_aggregation == Aggregation.avg

    for name in ("num_reviews_written", "number_of_orders", "referral_count"):
        sem = classify(pd.Series([0, 3, 7, 1, 9, 2] * 20), name)
        assert sem.analytical_role == AnalyticalRole.measure, name
        assert sem.default_aggregation == Aggregation.sum, name

    assert classify(pd.Series([3, 14, 27] * 20), "tenure_months").default_aggregation == Aggregation.avg


def test_numeric_metric_about_email_is_not_personal_data():
    rate = classify(pd.Series([0.12, 0.4, 0.33] * 20), "email_open_rate")
    assert not rate.is_sensitive
    assert rate.analytical_role == AnalyticalRole.percentage
    # The address itself is still personal data, and so is a bare numeric phone column.
    assert classify_sensitive("customer_email", pd.Series(["a@b.com", "c@d.org"])) == "email"
    assert classify_sensitive("phone", pd.Series([4155550100, 4155550101])) == "phone"


def test_dates_are_detected_from_strings():
    sem = classify(pd.Series(["2024-01-05", "2024-02-11", "2024-03-02"] * 20), "invoice_date")
    assert sem.physical_type in (PhysicalType.date, PhysicalType.datetime_)
    assert sem.analytical_role == AnalyticalRole.datetime_dimension
    assert sem.hierarchy and "Year" in sem.hierarchy


def test_low_cardinality_integers_behave_as_categories():
    sem = classify(pd.Series([1, 2, 3] * 60), "priority_level")
    assert sem.analytical_role == AnalyticalRole.categorical_dimension


def test_free_text_is_not_a_dimension():
    text = "The customer called to report a delayed shipment and asked for a partial refund today."
    sem = classify(pd.Series([text] * 40), "comments")
    assert sem.analytical_role == AnalyticalRole.free_text


def test_sensitive_detection_and_masking():
    assert classify_sensitive("customer_email", pd.Series(["a@b.com", "c@d.org"])) == "email"
    assert classify_sensitive("order_date", pd.Series(pd.to_datetime(["2024-01-01", "2024-02-01"]))) is None
    assert classify_sensitive("order_date", pd.Series(["2024-01-01", "2024-02-01"])) is None
    masked = mask_value("analyst@example.com", "email")
    assert masked.startswith("a***@") and "analyst" not in masked


# --------------------------------------------------------------------------- #
# Profiling
# --------------------------------------------------------------------------- #


def test_overview_counts_are_exact(session_obj, sample_frame):
    overview = session_obj.overview
    assert overview.row_count == len(sample_frame)
    assert overview.column_count == sample_frame.shape[1]
    assert overview.duplicate_row_count == int(sample_frame.duplicated().sum())
    assert overview.missing_cell_count == int(sample_frame.isna().sum().sum())


def test_category_column_count_differs_from_value_count(session_obj):
    overview = session_obj.overview
    assert overview.categorical_column_count < overview.distinct_category_values


def test_quality_score_is_explainable(session_obj):
    quality = session_obj.overview.quality
    assert 0 <= quality.score <= 100
    assert abs(sum(c.weight for c in quality.components) - 1.0) < 1e-9
    recomputed = sum(c.weight * c.score for c in quality.components)
    assert abs(recomputed - quality.score) < 0.1
    assert all(c.detail for c in quality.components)


def test_numeric_stats_are_populated(session_obj):
    revenue = next(c for c in session_obj.columns if c.name == "revenue_amount")
    assert revenue.numeric is not None
    assert revenue.numeric.max >= revenue.numeric.p75 >= revenue.numeric.median
    assert revenue.numeric.outlier_count_iqr >= 3
    # 5 seeded nulls, plus 3 from the duplicated rows appended in the fixture
    assert revenue.null_count == 8


def test_sensitive_samples_are_masked(session_obj):
    email = next(c for c in session_obj.columns if c.name == "customer_email")
    assert email.is_sensitive
    assert all("@example.com" not in v or v.count("*") > 0 for v in email.sample_values)


def test_profiling_is_reproducible(sample_frame):
    from app.core.ingestion import IngestionInfo
    from app.core.pipeline import build_session

    info = IngestionInfo(filename="a.csv", detected_format="text", total_rows=len(sample_frame))
    first = build_session("a", sample_frame.copy(), info, "")
    second = build_session("b", sample_frame.copy(), info, "")
    assert first.overview.quality.score == second.overview.quality.score
    assert [c.analytical_role for c in first.columns] == [c.analytical_role for c in second.columns]
    assert [a.kind for a in first.anomalies] == [a.kind for a in second.anomalies]
