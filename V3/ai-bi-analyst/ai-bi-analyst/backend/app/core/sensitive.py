"""Detect likely sensitive columns and mask any value that leaves the backend."""

from __future__ import annotations

import re

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[\d][\d\s().-]{6,}\d$")
IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
CARD_RE = re.compile(r"^(?:\d[ -]?){13,19}$")
SSN_RE = re.compile(r"^\d{3}-?\d{2}-?\d{4}$")

NAME_HINTS: dict[str, tuple[str, ...]] = {
    "email": ("email", "e_mail", "mail_id", "emailaddress"),
    "phone": ("phone", "mobile", "cell", "telephone", "msisdn", "fax"),
    "government_id": ("ssn", "social_security", "nationalid", "national_id", "passport", "aadhaar", "pan_no", "tax_id"),
    "account": ("account_no", "account_number", "acct", "iban", "card_no", "cardnumber", "creditcard", "bank_account"),
    "person_name": ("first_name", "last_name", "full_name", "customer_name", "employee_name", "contact_name"),
    "address": ("address", "street", "addr_line", "postal_address"),
    "credential": ("password", "secret", "token", "api_key", "apikey"),
    "health": ("diagnosis", "icd_code", "medical", "patient"),
}


#: A numeric column whose name also carries one of these tokens is a metric
#: about the hinted thing (`email_open_rate`, `phone_calls_count`), not the
#: personal value itself.
METRIC_TOKEN_RE = re.compile(
    r"(^|_)(rate|rates|pct|percent|percentage|ratio|share|count|cnt|num|number_of|score|avg|average|mean|"
    r"total|sum|opens?|clicks?|sent|views|frequency|duration|minutes|seconds|calls)($|_)"
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower())


DATE_LIKE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")


def classify_sensitive(name: str, series: pd.Series) -> str | None:
    """Return a sensitivity kind, or None. Name hints first, then value patterns."""
    n = _norm(name)
    numeric_metric = (
        pd.api.types.is_numeric_dtype(series)
        and not pd.api.types.is_bool_dtype(series)
        and METRIC_TOKEN_RE.search(n) is not None
    )
    if not numeric_metric:
        for kind, hints in NAME_HINTS.items():
            if any(h in n for h in hints):
                return kind

    if pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_float_dtype(series):
        # Decimal measures can look like loose digit patterns; only the name can
        # tell us a float column is sensitive, and that was checked above.
        return None

    sample = series.dropna().astype(str).head(200)
    if sample.empty:
        return None
    # Dates and timestamps can match loose digit patterns; never treat them as PII.
    if sum(1 for v in sample if DATE_LIKE_RE.match(v.strip())) / len(sample) > 0.5:
        return None
    checks = (
        ("email", EMAIL_RE),
        ("government_id", SSN_RE),
        ("account", IBAN_RE),
        ("account", CARD_RE),
        ("phone", PHONE_RE),
    )
    for kind, pattern in checks:
        hits = sum(1 for v in sample if pattern.match(v.strip()))
        if hits / len(sample) >= 0.6:
            return kind
    return None


def mask_value(value: object, kind: str | None = None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "(null)"
    text = str(value)
    if kind == "email" and "@" in text:
        user, _, domain = text.partition("@")
        return f"{user[:1]}***@{domain}"
    if len(text) <= 2:
        return "*" * len(text)
    if len(text) <= 6:
        return f"{text[0]}{'*' * (len(text) - 1)}"
    return f"{text[:2]}{'*' * 6}{text[-2:]}"


def mask_series_samples(series: pd.Series, kind: str | None, n: int = 5) -> list[str]:
    values = series.dropna().unique()[:n]
    return [mask_value(v, kind) for v in values]
