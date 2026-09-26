"""Governed data-quality rules.

The distinction this module exists to keep is between two different kinds of
statement:

  * an **anomaly** is a statistical suspicion — "this value is far from the
    others", which may be perfectly correct data;
  * a **rule violation** is a confirmed breach of a constraint a human agreed
    to — "this column must never be null", "this code must be one of these six
    values".

So every result here is tagged `fact`, carries the exact expression it
evaluated, and names the remediation. Rules are proposed from the profile but
only ever *enabled* by a person, which is what makes a breach defensible.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..schemas import (
    AnalyticalRole,
    Anomaly,
    ColumnProfile,
    EvidenceKind,
    PhysicalType,
    QualityReport,
    QualityRule,
    RuleResult,
    Severity,
)
from .ingestion import neutralize_csv_value
from .semantics import ColumnSemantics
from .sensitive import mask_value

log = logging.getLogger("ai_bi_analyst.quality_rules")

MAX_SAMPLE_VALUES = 5
MAX_ACCEPTED_VALUES = 50
MAX_PATTERN_LENGTH = 200

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"


class RuleError(ValueError):
    """An unusable rule. The message is safe to show to a user."""


# --------------------------------------------------------------------------- #
# Suggestions
# --------------------------------------------------------------------------- #


def suggest_rules(semantics: list[ColumnSemantics], profiles: list[ColumnProfile]) -> list[QualityRule]:
    """Propose rules from the profile. Nothing is enabled without a decision.

    A suggestion is only made where the data itself is strong evidence of an
    intended constraint: a column that is already complete probably must stay
    complete; a column that is already unique is probably a key.
    """
    by_name = {p.name: p for p in profiles}
    out: list[QualityRule] = []

    for sem in semantics:
        profile = by_name.get(sem.name)
        if profile is None or sem.analytical_role == AnalyticalRole.unusable:
            continue

        if profile.null_percent == 0 and sem.analytical_role in (
            AnalyticalRole.identifier,
            AnalyticalRole.datetime_dimension,
            AnalyticalRole.currency,
            AnalyticalRole.measure,
        ):
            out.append(
                QualityRule(
                    id=f"rule-required-{sem.name}",
                    column=sem.name,
                    kind="required",
                    enabled=False,
                    severity=Severity.high,
                    description=f"'{sem.label}' is currently complete, so it is probably mandatory.",
                    origin="suggested",
                    remediation="Fix the extract that leaves this column empty, or agree an explicit default.",
                )
            )

        if sem.analytical_role == AnalyticalRole.identifier and sem.uniqueness_ratio >= 0.99:
            out.append(
                QualityRule(
                    id=f"rule-unique-{sem.name}",
                    column=sem.name,
                    kind="unique",
                    enabled=False,
                    severity=Severity.high,
                    description=f"'{sem.label}' is almost entirely unique, so it looks like the row key.",
                    origin="suggested",
                    remediation="Confirm the intended grain and de-duplicate, or define the true composite key.",
                )
            )

        if (
            sem.analytical_role in (AnalyticalRole.categorical_dimension, AnalyticalRole.geography)
            and profile.categorical
            and 2 <= sem.distinct_count <= 12
        ):
            allowed = sorted({top.value for top in profile.categorical.top_values if top.value})
            if allowed:
                out.append(
                    QualityRule(
                        id=f"rule-accepted-{sem.name}",
                        column=sem.name,
                        kind="accepted_values",
                        enabled=False,
                        severity=Severity.medium,
                        allowed=allowed,
                        description=(
                            f"'{sem.label}' has {len(allowed)} observed members; treat them as the agreed list."
                        ),
                        origin="suggested",
                        remediation="Add any missing valid member, or map the stray values to a valid one on load.",
                    )
                )

        if profile.numeric and sem.analytical_role in (
            AnalyticalRole.measure,
            AnalyticalRole.currency,
            AnalyticalRole.percentage,
        ):
            floor = 0.0 if (profile.numeric.min or 0) >= 0 else None
            if floor is not None:
                out.append(
                    QualityRule(
                        id=f"rule-range-{sem.name}",
                        column=sem.name,
                        kind="numeric_range",
                        enabled=False,
                        severity=Severity.medium,
                        min_value=0.0,
                        max_value=None,
                        description=f"'{sem.label}' is never negative in this file. Confirm whether it can be.",
                        origin="suggested",
                        remediation="If credits or returns are valid, split them into their own measure.",
                    )
                )

        if sem.sensitive_kind == "email":
            out.append(
                QualityRule(
                    id=f"rule-format-{sem.name}",
                    column=sem.name,
                    kind="regex_format",
                    enabled=False,
                    severity=Severity.medium,
                    pattern=EMAIL_PATTERN,
                    description=f"'{sem.label}' looks like an email address column.",
                    origin="suggested",
                    remediation="Reject malformed addresses at the point of capture.",
                )
            )

        if profile.date and profile.date.future_count > 0:
            out.append(
                QualityRule(
                    id=f"rule-dates-{sem.name}",
                    column=sem.name,
                    kind="date_range",
                    enabled=False,
                    severity=Severity.medium,
                    allow_future=False,
                    description=f"'{sem.label}' contains {profile.date.future_count} future dates.",
                    origin="suggested",
                    remediation="Confirm whether future dates are valid here; if not, correct them at source.",
                )
            )

    return out


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_rules(rules: list[QualityRule], semantics: list[ColumnSemantics]) -> tuple[list[QualityRule], list[str]]:
    """Keep only rules that can be evaluated against the current schema."""
    known = {s.name: s for s in semantics}
    kept: list[QualityRule] = []
    dropped: list[str] = []

    for rule in rules:
        if rule.column not in known:
            dropped.append(rule.column)
            continue
        if rule.kind == "accepted_values" and not rule.allowed:
            raise RuleError(f"The accepted-values rule on '{rule.column}' needs at least one allowed value.")
        if rule.kind == "accepted_values" and len(rule.allowed) > MAX_ACCEPTED_VALUES:
            raise RuleError(
                f"The accepted-values rule on '{rule.column}' lists more than {MAX_ACCEPTED_VALUES} values."
            )
        if rule.kind == "numeric_range" and rule.min_value is None and rule.max_value is None:
            raise RuleError(f"The range rule on '{rule.column}' needs a minimum, a maximum, or both.")
        if rule.kind == "regex_format":
            if not rule.pattern:
                raise RuleError(f"The format rule on '{rule.column}' needs a pattern.")
            if len(rule.pattern) > MAX_PATTERN_LENGTH:
                raise RuleError(f"The pattern on '{rule.column}' is too long.")
            try:
                re.compile(rule.pattern)
            except re.error as exc:
                raise RuleError(f"The pattern on '{rule.column}' is not a valid regular expression: {exc}.") from exc
        if not 0.0 <= rule.max_fail_percent <= 100.0:
            raise RuleError(f"The tolerance on '{rule.column}' must be between 0 and 100 percent.")
        kept.append(rule)

    return kept, dropped


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _mask_samples(series: pd.Series, sem: ColumnSemantics) -> list[str]:
    values = series.dropna().astype(str).unique()[:MAX_SAMPLE_VALUES]
    if sem.is_sensitive:
        return [mask_value(value, sem.sensitive_kind) for value in values]
    return [value[:60] for value in values]


def _skip(rule: QualityRule, sem: ColumnSemantics, reason: str) -> RuleResult:
    return RuleResult(
        rule_id=rule.id,
        column=rule.column,
        kind=rule.kind,
        label=f"{sem.label}: {rule.kind.replace('_', ' ')}",
        expression="not evaluated",
        severity=rule.severity,
        passed=True,
        evaluated_rows=0,
        passed_rows=0,
        failed_rows=0,
        failed_percent=0.0,
        skipped_reason=reason,
        remediation=rule.remediation,
    )


def evaluate_rule(df: pd.DataFrame, rule: QualityRule, sem: ColumnSemantics) -> RuleResult:
    """Evaluate one rule and report the exact expression that produced the count."""
    series = df[rule.column]
    total = len(series)

    if rule.kind == "required":
        text = series.astype(str).str.strip()
        failed_mask = series.isna() | (series.notna() & text.eq(""))
        expression = f'"{rule.column}" IS NOT NULL AND TRIM("{rule.column}") <> \'\''
        evaluated = total

    elif rule.kind == "unique":
        present = series.dropna()
        failed_mask = pd.Series(False, index=series.index)
        failed_mask.loc[present.index] = present.duplicated(keep=False)
        expression = f'COUNT("{rule.column}") = COUNT(DISTINCT "{rule.column}")'
        evaluated = int(present.shape[0])

    elif rule.kind == "accepted_values":
        allowed = {value.strip().casefold() for value in rule.allowed}
        present = series.dropna()
        compared = present.astype(str).str.strip().str.casefold()
        failed_mask = pd.Series(False, index=series.index)
        failed_mask.loc[present.index] = ~compared.isin(allowed)
        shown = ", ".join(rule.allowed[:6]) + (" …" if len(rule.allowed) > 6 else "")
        # Compared trimmed and case-insensitively on purpose: a casing variant is
        # a consistency anomaly, not a breach of the agreed member list.
        expression = f'"{rule.column}" IN ({shown}) -- trimmed, case-insensitive'
        evaluated = int(present.shape[0])

    elif rule.kind == "numeric_range":
        numeric = pd.to_numeric(series, errors="coerce")
        present = numeric.dropna()
        if present.empty:
            return _skip(rule, sem, "No numeric values to check.")
        below = (
            ((present < rule.min_value) if rule.inclusive else (present <= rule.min_value))
            if rule.min_value is not None
            else pd.Series(False, index=present.index)
        )
        above = (
            ((present > rule.max_value) if rule.inclusive else (present >= rule.max_value))
            if rule.max_value is not None
            else pd.Series(False, index=present.index)
        )
        failed_mask = pd.Series(False, index=series.index)
        failed_mask.loc[present.index] = below | above
        bounds = []
        if rule.min_value is not None:
            bounds.append(f'"{rule.column}" >{"=" if rule.inclusive else ""} {rule.min_value:g}')
        if rule.max_value is not None:
            bounds.append(f'"{rule.column}" <{"=" if rule.inclusive else ""} {rule.max_value:g}')
        expression = " AND ".join(bounds)
        evaluated = int(present.shape[0])

    elif rule.kind == "regex_format":
        pattern = re.compile(rule.pattern or "")
        present = series.dropna()
        matched = present.astype(str).str.strip().str.fullmatch(pattern)
        failed_mask = pd.Series(False, index=series.index)
        failed_mask.loc[present.index] = ~matched.fillna(False)
        expression = f'"{rule.column}" MATCHES /{rule.pattern}/'
        evaluated = int(present.shape[0])

    elif rule.kind == "date_range":
        parsed = sem.parsed_datetime if sem.parsed_datetime is not None else pd.to_datetime(series, errors="coerce")
        present = parsed.dropna()
        if present.empty:
            return _skip(rule, sem, "No parseable dates to check.")
        checks = pd.Series(False, index=present.index)
        bounds = []
        if rule.earliest:
            try:
                floor = pd.Timestamp(rule.earliest)
            except ValueError as exc:
                raise RuleError(f"'{rule.earliest}' is not a date the rule can use.") from exc
            checks |= present < floor
            bounds.append(f'"{rule.column}" >= {floor.date()}')
        if rule.latest:
            try:
                ceiling = pd.Timestamp(rule.latest)
            except ValueError as exc:
                raise RuleError(f"'{rule.latest}' is not a date the rule can use.") from exc
            checks |= present > ceiling
            bounds.append(f'"{rule.column}" <= {ceiling.date()}')
        if not rule.allow_future:
            now = pd.Timestamp.now()
            checks |= present > now
            bounds.append(f'"{rule.column}" <= today')
        if not bounds:
            return _skip(rule, sem, "The rule sets no date bound to check.")
        failed_mask = pd.Series(False, index=series.index)
        failed_mask.loc[present.index] = checks
        expression = " AND ".join(bounds)
        evaluated = int(present.shape[0])

    else:  # pragma: no cover - the schema constrains the kind
        raise RuleError(f"Unsupported rule kind '{rule.kind}'.")

    failed_rows = int(failed_mask.sum())
    failed_percent = round(100 * failed_rows / total, 3) if total else 0.0

    return RuleResult(
        rule_id=rule.id,
        column=rule.column,
        kind=rule.kind,
        label=f"{sem.label}: {rule.kind.replace('_', ' ')}",
        expression=expression,
        severity=rule.severity,
        # A tolerance of 0 means any failure is a breach.
        passed=failed_percent <= rule.max_fail_percent,
        evaluated_rows=evaluated,
        passed_rows=max(0, evaluated - failed_rows),
        failed_rows=failed_rows,
        failed_percent=failed_percent,
        sample_values=_mask_samples(series[failed_mask], sem),
        remediation=rule.remediation,
        evidence_kind=EvidenceKind.fact,
    )


def evaluate_rules(
    df: pd.DataFrame,
    rules: list[QualityRule],
    semantics: list[ColumnSemantics],
    *,
    dataset_id: str,
    sampled: bool,
    anomalies: list[Anomaly] | None = None,
) -> QualityReport:
    by_name = {s.name: s for s in semantics}
    results: list[RuleResult] = []

    for rule in rules:
        if not rule.enabled:
            continue
        sem = by_name.get(rule.column)
        if sem is None:
            continue
        if sem.physical_type == PhysicalType.unsupported:
            results.append(_skip(rule, sem, "The column type cannot be checked."))
            continue
        try:
            results.append(evaluate_rule(df, rule, sem))
        except RuleError as exc:
            log.info("rule skipped dataset=%s rule=%s reason=%s", dataset_id, rule.id, exc)
            results.append(_skip(rule, sem, str(exc)))

    order = {Severity.high: 0, Severity.medium: 1, Severity.low: 2, Severity.info: 3}
    results.sort(key=lambda r: (r.passed, order[r.severity], -r.failed_percent))

    skipped = [r for r in results if r.skipped_reason]
    failed = [r for r in results if not r.passed and not r.skipped_reason]

    suspicions = [
        f"{a.title} ({a.method})"
        for a in (anomalies or [])
        if a.evidence_kind in (EvidenceKind.inference, EvidenceKind.assumption)
    ][:8]

    return QualityReport(
        dataset_id=dataset_id,
        evaluated_at=datetime.now(UTC),
        analyzed_rows=len(df),
        sampled=sampled,
        rules_evaluated=len(results),
        rules_passed=len([r for r in results if r.passed and not r.skipped_reason]),
        rules_failed=len(failed),
        rules_skipped=len(skipped),
        results=results,
        suspicions=suspicions,
    )


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def report_csv(report: QualityReport, filename: str) -> str:
    """A concise report with no hidden sensitive values and no live formulas."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(["AI BI Analyst data-quality report"])
    writer.writerow(["Source file", neutralize_csv_value(filename)])
    writer.writerow(["Evaluated at (UTC)", report.evaluated_at.strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow(["Rows analysed", report.analyzed_rows])
    writer.writerow(["Based on a sample", "yes" if report.sampled else "no"])
    writer.writerow(["Rules passed", report.rules_passed])
    writer.writerow(["Rules failed", report.rules_failed])
    writer.writerow(["Rules skipped", report.rules_skipped])
    writer.writerow([])
    writer.writerow(
        [
            "Column",
            "Rule",
            "Constraint",
            "Severity",
            "Outcome",
            "Rows checked",
            "Rows passed",
            "Rows failed",
            "Failed %",
            "Remediation",
            "Note",
        ]
    )
    for result in report.results:
        writer.writerow(
            [
                neutralize_csv_value(result.column),
                result.kind,
                neutralize_csv_value(result.expression),
                result.severity.value,
                "skipped" if result.skipped_reason else ("pass" if result.passed else "FAIL"),
                result.evaluated_rows,
                result.passed_rows,
                result.failed_rows,
                result.failed_percent,
                neutralize_csv_value(result.remediation),
                neutralize_csv_value(result.skipped_reason or ""),
            ]
        )
    if report.suspicions:
        writer.writerow([])
        writer.writerow(["Statistical suspicions (not rule breaches)"])
        for suspicion in report.suspicions:
            writer.writerow([neutralize_csv_value(suspicion)])
    writer.writerow([])
    writer.writerow(
        [
            "Failing values are deliberately omitted from this export. "
            "Open the rule in the application to see masked examples."
        ]
    )
    return buffer.getvalue()


def default_rule_id(column: str, kind: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
    return f"rule-{kind.replace('_', '-')}-{slug}"


def merge_rules(existing: list[QualityRule], incoming: list[QualityRule]) -> list[QualityRule]:
    """Incoming definitions win; suggestions the user has not touched survive."""
    merged: dict[str, QualityRule] = {rule.id: rule for rule in existing}
    for rule in incoming:
        merged[rule.id] = rule
    return sorted(merged.values(), key=lambda rule: (rule.column, rule.kind))


def rules_payload(rules: list[QualityRule]) -> list[dict[str, Any]]:
    return [rule.model_dump(mode="json") for rule in rules]
