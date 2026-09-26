"""Semantic classification of columns.

Physical type comes from the data. Analytical role combines the data shape with
name evidence, because a numeric column called `zip_code` is not a measure.
Every decision carries a `reason` string so the UI can explain itself, and the
user can override any of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..schemas import Aggregation, AnalyticalRole, PhysicalType
from .sensitive import classify_sensitive

ID_NAME_RE = re.compile(
    r"(^|_)(id|ids|key|code|no|num|number|nbr|uuid|guid|sku|isbn|ean|upc|serial|ref|reference)($|_)"
)
NON_MEASURE_NAME_RE = re.compile(
    r"(zip|zipcode|postal|postcode|phone|mobile|fax|ssn|account|acct|card|invoice_no|order_no|"
    r"door|house_no|year_month|fiscal_period|latitude|longitude|lat$|lon$|lng$|pincode)"
)
CURRENCY_NAME_RE = re.compile(
    r"(amount|amt|revenue|sales|price|cost|profit|margin_value|spend|budget|fee|salary|payment|"
    r"value_usd|gross|net_sales|discount_value|tax|balance|charge|income|earnings|wage|"
    r"transaction_value|order_value|basket_value|lifetime_value|(^|_)(aov|ltv|clv)($|_))"
)
PERCENT_NAME_RE = re.compile(r"(pct|percent|percentage|rate$|ratio|share|margin_pct|_pc$|utilization)")
AVERAGE_NAME_RE = re.compile(
    r"(score|rating|rank|index|nps|csat|likelihood|probability|temperature|(^|_)age($|_)|"
    r"(^|_)days($|_)|duration|latency|response_time|per_unit|unit_price|price_per|rate_per|cost_per|(^|_)price($|_)|"
    r"(^|_)(avg|average|mean|median)($|_)|frequency|tenure|"
    r"(^|_)(hours|minutes|mins|seconds|secs|weeks|months|years)($|_))"
)
#: `num_reviews`, `number_of_orders`, `referral_count`: a count of things, which
#: is summed. Checked before the identifier rule, whose `num`/`no` tokens would
#: otherwise claim these names.
COUNT_NAME_RE = re.compile(r"(^(num|number|no|nbr|cnt|count)_(of_)?[a-z])|(_(count|cnt)$)")
DATE_NAME_RE = re.compile(r"(date|dt$|_dt|day|month|year|quarter|week|timestamp|time$|_at$|_on$|period)")
GEO_NAME_RE = re.compile(
    r"(country|nation|region|state|province|city|town|county|district|territory|zone|"
    r"postal|zip|latitude|longitude|geo|market|continent)"
)
TEXT_NAME_RE = re.compile(r"(comment|description|notes?|remark|feedback|review|message|summary|text|detail)")
BOOL_TRUTHY = {"true", "false", "yes", "no", "y", "n", "t", "f", "1", "0"}

DATE_HIERARCHY = ["year", "quarter", "month", "week", "date"]
GEO_HIERARCHY = ["continent", "country", "region", "state", "province", "city", "postal"]


@dataclass
class ColumnSemantics:
    name: str
    label: str
    position: int
    physical_type: PhysicalType
    analytical_role: AnalyticalRole
    default_aggregation: Aggregation
    distinct_count: int
    non_null_count: int
    null_count: int
    uniqueness_ratio: float
    is_sensitive: bool = False
    sensitive_kind: str | None = None
    hierarchy: str | None = None
    hierarchy_level: int | None = None
    reason: str = ""
    parsed_datetime: pd.Series | None = field(default=None, repr=False)
    numeric_view: pd.Series | None = field(default=None, repr=False)


def humanize(name: str) -> str:
    text = re.sub(r"[_\-.]+", " ", str(name)).strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"\s+", " ", text)
    if text.isupper():
        return text
    return text[:1].upper() + text[1:]


def _try_datetime(series: pd.Series) -> pd.Series | None:
    """Parse strings to datetime only when most non-null values succeed."""
    sample = series.dropna()
    if sample.empty:
        return None
    head = sample.head(300).astype(str)
    if head.str.fullmatch(r"\d{1,4}([.,]\d+)?").mean() > 0.9:
        return None  # pure numbers: don't guess epochs
    try:
        parsed_head = pd.to_datetime(head, errors="coerce", format="mixed")
    except Exception:
        try:
            parsed_head = pd.to_datetime(head, errors="coerce")
        except Exception:
            return None
    if parsed_head.notna().mean() < 0.8:
        return None
    try:
        full = pd.to_datetime(series, errors="coerce", format="mixed")
    except Exception:
        full = pd.to_datetime(series, errors="coerce")
    return full


def _physical_type(series: pd.Series, parsed_dt: pd.Series | None) -> PhysicalType:
    if pd.api.types.is_bool_dtype(series):
        return PhysicalType.boolean
    if pd.api.types.is_datetime64_any_dtype(series):
        only_midnight = bool(
            series.dropna().empty
            or (
                (series.dt.hour.fillna(0) == 0).all()
                and (series.dt.minute.fillna(0) == 0).all()
                and (series.dt.second.fillna(0) == 0).all()
            )
        )
        return PhysicalType.date if only_midnight else PhysicalType.datetime_
    if pd.api.types.is_integer_dtype(series):
        return PhysicalType.integer
    if pd.api.types.is_float_dtype(series):
        return PhysicalType.float
    if parsed_dt is not None:
        only_midnight = bool(
            parsed_dt.dropna().empty
            or (
                (parsed_dt.dt.hour.fillna(0) == 0).all()
                and (parsed_dt.dt.minute.fillna(0) == 0).all()
                and (parsed_dt.dt.second.fillna(0) == 0).all()
            )
        )
        return PhysicalType.date if only_midnight else PhysicalType.datetime_
    if pd.api.types.is_object_dtype(series) or isinstance(series.dtype, pd.StringDtype):
        return PhysicalType.string
    return PhysicalType.unsupported


def _looks_boolean(series: pd.Series) -> bool:
    vals = {str(v).strip().lower() for v in series.dropna().unique()[:20]}
    return 0 < len(vals) <= 2 and vals.issubset(BOOL_TRUTHY)


def classify_column(series: pd.Series, name: str, position: int, total_rows: int) -> ColumnSemantics:
    norm = re.sub(r"[^a-z0-9]+", "_", str(name).lower())
    non_null = int(series.notna().sum())
    nulls = int(len(series) - non_null)
    distinct = int(series.nunique(dropna=True))
    uniqueness = (distinct / non_null) if non_null else 0.0

    parsed_dt = None
    if not pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        parsed_dt = _try_datetime(series)
    ptype = _physical_type(series, parsed_dt)

    numeric_view: pd.Series | None = None
    if ptype in (PhysicalType.integer, PhysicalType.float, PhysicalType.decimal):
        numeric_view = pd.to_numeric(series, errors="coerce")

    kind = classify_sensitive(str(name), series)
    role, agg, reason = _role_for(name, norm, series, ptype, distinct, uniqueness, non_null, total_rows, kind)

    hierarchy, level = _hierarchy_for(norm, role)

    return ColumnSemantics(
        name=str(name),
        label=humanize(name),
        position=position,
        physical_type=ptype,
        analytical_role=role,
        default_aggregation=agg,
        distinct_count=distinct,
        non_null_count=non_null,
        null_count=nulls,
        uniqueness_ratio=round(uniqueness, 4),
        is_sensitive=kind is not None,
        sensitive_kind=kind,
        hierarchy=hierarchy,
        hierarchy_level=level,
        reason=reason,
        parsed_datetime=parsed_dt if ptype in (PhysicalType.date, PhysicalType.datetime_) else None,
        numeric_view=numeric_view,
    )


def _role_for(
    name: str,
    norm: str,
    series: pd.Series,
    ptype: PhysicalType,
    distinct: int,
    uniqueness: float,
    non_null: int,
    total_rows: int,
    sensitive_kind: str | None,
) -> tuple[AnalyticalRole, Aggregation, str]:
    if non_null == 0:
        return AnalyticalRole.unusable, Aggregation.none, "Column is entirely empty."

    if sensitive_kind in {"email", "phone", "government_id", "account", "credential"}:
        return (
            AnalyticalRole.sensitive,
            Aggregation.count_distinct,
            f"Values match a {sensitive_kind.replace('_', ' ')} pattern, so the column is treated as sensitive.",
        )

    if ptype in (PhysicalType.date, PhysicalType.datetime_):
        return AnalyticalRole.datetime_dimension, Aggregation.none, "Values parse as dates or timestamps."

    if ptype == PhysicalType.boolean or _looks_boolean(series):
        return AnalyticalRole.categorical_dimension, Aggregation.count, "Two-valued flag column."

    if ptype in (PhysicalType.integer, PhysicalType.float, PhysicalType.decimal):
        if distinct > 2 and COUNT_NAME_RE.search(norm) and not re.search(r"(^|_)(id|key|code)$", norm):
            return AnalyticalRole.measure, Aggregation.sum, "Name indicates a count of things, so it is summed."
        if uniqueness > 0.95 and ID_NAME_RE.search(norm):
            return (
                AnalyticalRole.identifier,
                Aggregation.count_distinct,
                "Near-unique numeric values with an identifier-style name.",
            )
        if NON_MEASURE_NAME_RE.search(norm):
            return (
                AnalyticalRole.geography if GEO_NAME_RE.search(norm) else AnalyticalRole.identifier,
                Aggregation.count_distinct,
                "Numeric but the name indicates a code or locator, so it is not aggregated as a measure.",
            )
        if PERCENT_NAME_RE.search(norm):
            return (
                AnalyticalRole.percentage,
                Aggregation.avg,
                "Name indicates a rate or percentage; averaging is safer than summing.",
            )
        if ID_NAME_RE.search(norm):
            return (
                AnalyticalRole.identifier,
                Aggregation.count_distinct,
                "Identifier-style name, so it is counted rather than summed even though it is stored as a number.",
            )
        if CURRENCY_NAME_RE.search(norm) and AVERAGE_NAME_RE.search(norm):
            return (
                AnalyticalRole.currency,
                Aggregation.avg,
                "Monetary but stated per unit, so the default is an average.",
            )
        if CURRENCY_NAME_RE.search(norm):
            return AnalyticalRole.currency, Aggregation.sum, "Name indicates a monetary amount."
        if AVERAGE_NAME_RE.search(norm):
            return (
                AnalyticalRole.measure,
                Aggregation.avg,
                "Name indicates a score or per-item value, so averaging is the safer default.",
            )
        if distinct <= 2 and set(pd.to_numeric(series, errors="coerce").dropna().unique()).issubset({0, 1}):
            return AnalyticalRole.categorical_dimension, Aggregation.count, "Only 0/1 values, so treated as a flag."
        if distinct <= 12 and ptype == PhysicalType.integer and uniqueness < 0.05:
            return (
                AnalyticalRole.categorical_dimension,
                Aggregation.count,
                f"Only {distinct} distinct integers across {non_null:,} rows, which behaves like a category.",
            )
        return AnalyticalRole.measure, Aggregation.sum, "Continuous numeric values suitable for aggregation."

    # Strings
    text = series.dropna().astype(str)
    mean_len = float(text.str.len().mean()) if not text.empty else 0.0
    words = float(text.head(300).str.split().str.len().mean() or 0)
    if uniqueness > 0.9 and (ID_NAME_RE.search(norm) or mean_len <= 36):
        return (
            AnalyticalRole.identifier,
            Aggregation.count_distinct,
            "Almost every value is unique, which is typical of a key.",
        )
    if TEXT_NAME_RE.search(norm) or (mean_len > 60 and words > 8):
        return AnalyticalRole.free_text, Aggregation.count, "Long free-form values; not usable as a grouping dimension."
    if GEO_NAME_RE.search(norm):
        return AnalyticalRole.geography, Aggregation.count_distinct, "Name indicates a geographic attribute."
    if DATE_NAME_RE.search(norm) and distinct <= 400:
        return (
            AnalyticalRole.categorical_dimension,
            Aggregation.count,
            "Date-like label stored as text; treated as a period dimension.",
        )
    return (
        AnalyticalRole.categorical_dimension,
        Aggregation.count,
        f"{distinct:,} distinct text values, suitable for grouping.",
    )


def _hierarchy_for(norm: str, role: AnalyticalRole) -> tuple[str | None, int | None]:
    if role == AnalyticalRole.datetime_dimension:
        return "Time: Year > Quarter > Month > Date", 0
    for idx, level in enumerate(DATE_HIERARCHY):
        if re.search(rf"(^|_){level}($|_)", norm):
            return "Time: Year > Quarter > Month > Date", idx
    for idx, level in enumerate(GEO_HIERARCHY):
        if re.search(rf"(^|_){level}($|_)", norm) or norm == level:
            return "Geography: Continent > Country > Region > State > City", idx
    if re.search(r"(category|sub_category|subcategory|product|segment|department|division|group|class)", norm):
        return "Product/Org: Division > Category > Sub-category > Item", None
    return None, None


def detect_hierarchies(semantics: list[ColumnSemantics]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for s in semantics:
        if s.hierarchy:
            groups.setdefault(s.hierarchy, []).append(s.name)
    return {k: v for k, v in groups.items() if len(v) >= 1}


def apply_overrides(semantics: list[ColumnSemantics], overrides: dict[str, dict]) -> list[ColumnSemantics]:
    by_name = {s.name: s for s in semantics}
    for name, patch in overrides.items():
        s = by_name.get(name)
        if not s:
            continue
        if patch.get("analytical_role"):
            s.analytical_role = AnalyticalRole(patch["analytical_role"])
            s.reason = "Set by the user."
        if patch.get("default_aggregation"):
            s.default_aggregation = Aggregation(patch["default_aggregation"])
        if patch.get("label"):
            s.label = patch["label"]
        if patch.get("is_sensitive") is not None:
            s.is_sensitive = bool(patch["is_sensitive"])
    return semantics


def numeric_columns(semantics: list[ColumnSemantics]) -> list[str]:
    return [
        s.name
        for s in semantics
        if s.analytical_role in (AnalyticalRole.measure, AnalyticalRole.currency, AnalyticalRole.percentage)
    ]


def categorical_columns(semantics: list[ColumnSemantics]) -> list[str]:
    return [
        s.name
        for s in semantics
        if s.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)
    ]


def date_columns(semantics: list[ColumnSemantics]) -> list[str]:
    return [s.name for s in semantics if s.analytical_role == AnalyticalRole.datetime_dimension]


def safe_float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return round(f, 6)
