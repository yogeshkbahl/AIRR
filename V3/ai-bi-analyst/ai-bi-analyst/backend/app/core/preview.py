"""Dataset preview.

Paginated, sortable and filterable, with sensitive columns masked before any
row leaves the backend. The filter reuses the same `FilterOp` allow-list as the
governed query plan, and the column must exist, so a preview request cannot be
turned into an arbitrary expression.
"""

from __future__ import annotations

import re

import pandas as pd

from ..schemas import DatasetPreview, PreviewRequest
from .query_plan import PlanError, _jsonable
from .semantics import ColumnSemantics
from .sensitive import mask_value

MAX_PREVIEW_ROWS = 200


def _apply_filter(df: pd.DataFrame, request: PreviewRequest, column: str) -> pd.Series:
    """Build a boolean mask from an allow-listed operator."""
    series = df[column]
    op = request.filter_op
    value = request.filter_value

    if op == "is_null":
        return series.isna()
    if op == "not_null":
        return series.notna()

    if op in ("in", "not_in"):
        values = value if isinstance(value, (list, tuple)) else [value]
        text = series.astype(str)
        mask = text.isin([str(item) for item in values])
        return ~mask if op == "not_in" else mask

    if op == "between":
        values = list(value or [])
        if len(values) != 2:
            raise PlanError("A 'between' filter needs exactly two values.")
        numeric = pd.to_numeric(series, errors="coerce")
        low, high = pd.to_numeric(pd.Series(values), errors="coerce")
        if pd.isna(low) or pd.isna(high):
            return series.astype(str).between(str(values[0]), str(values[1]))
        return numeric.between(low, high)

    numeric = pd.to_numeric(series, errors="coerce")
    comparable = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]

    if op == "eq":
        if pd.notna(comparable):
            return numeric.eq(comparable)
        # Case-insensitive contains is the useful reading of "equals" for a
        # free-text search box, so it is stated in the response note.
        return series.astype(str).str.contains(re.escape(str(value)), case=False, na=False)
    if op == "neq":
        if pd.notna(comparable):
            return numeric.ne(comparable)
        return ~series.astype(str).str.contains(re.escape(str(value)), case=False, na=False)

    if pd.isna(comparable):
        raise PlanError(f"'{value}' is not a number, so it cannot be compared with '{op}'.")
    if op == "gt":
        return numeric.gt(comparable)
    if op == "gte":
        return numeric.ge(comparable)
    if op == "lt":
        return numeric.lt(comparable)
    if op == "lte":
        return numeric.le(comparable)

    raise PlanError(f"Unsupported filter operator '{op}'.")


def build_preview(
    df: pd.DataFrame,
    semantics: list[ColumnSemantics],
    request: PreviewRequest,
    *,
    dataset_id: str,
) -> DatasetPreview:
    known = {s.name: s for s in semantics}
    notes: list[str] = []

    working = df
    filtered_rows = len(df)
    if request.filter_column and request.filter_op:
        if request.filter_column not in known:
            raise PlanError(f"'{request.filter_column}' is not a column in this dataset.")
        if known[request.filter_column].is_sensitive:
            raise PlanError(
                f"'{known[request.filter_column].label}' holds personal data, so it cannot be filtered on here."
            )
        mask = _apply_filter(df, request, request.filter_column)
        working = df[mask.fillna(False)]
        filtered_rows = len(working)
        if (
            request.filter_op in ("eq", "neq")
            and pd.to_numeric(pd.Series([request.filter_value]), errors="coerce").isna().iloc[0]
        ):
            notes.append("Text equality is matched as a case-insensitive contains.")

    sort_by = request.sort_by
    if sort_by:
        if sort_by not in known:
            raise PlanError(f"'{sort_by}' is not a column in this dataset.")
        working = working.sort_values(by=sort_by, ascending=not request.sort_desc, kind="stable")

    limit = min(request.limit, MAX_PREVIEW_ROWS)
    page = working.iloc[request.offset : request.offset + limit].copy()

    masked_columns = [s.name for s in semantics if s.is_sensitive]
    for name in masked_columns:
        if name in page.columns:
            kind = known[name].sensitive_kind
            page[name] = page[name].map(lambda value, kind=kind: mask_value(value, kind))
    if masked_columns:
        notes.append(f"{len(masked_columns)} column(s) holding personal data are masked in this view.")

    return DatasetPreview(
        dataset_id=dataset_id,
        columns=[str(column) for column in page.columns],
        rows=_jsonable(page),
        offset=request.offset,
        limit=limit,
        returned_rows=len(page),
        total_rows=len(df),
        filtered_rows=filtered_rows,
        sort_by=sort_by,
        sort_desc=request.sort_desc,
        masked_columns=masked_columns,
        note=" ".join(notes),
    )
