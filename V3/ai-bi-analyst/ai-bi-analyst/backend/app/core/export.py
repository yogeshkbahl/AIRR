"""Storyboard exports. Everything rendered is HTML-escaped; CSV values are neutralised."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from ..schemas import Storyboard
from .query_plan import chart_data


def storyboard_definition(board: Storyboard, session) -> dict:
    return {
        "format": "ai-bi-analyst.storyboard/v1",
        "exported_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "id": session.id,
            "filename": session.filename,
            "row_count": session.overview.row_count,
            "column_count": session.overview.column_count,
            "sampled": session.overview.sampled,
            "quality_score": session.overview.quality.score,
        },
        "storyboard": board.model_dump(mode="json"),
        "semantic_layer": [
            {
                "name": s.name,
                "label": s.label,
                "role": s.analytical_role.value,
                "default_aggregation": s.default_aggregation.value,
                "hierarchy": s.hierarchy,
            }
            for s in session.semantics
        ],
        "governance": {
            "note": "Recommendations and generated narrative require business validation before publication.",
            "profile_version": "profile-v1.2",
        },
    }


def _table_html(columns: list[str], rows: list[dict]) -> str:
    if not columns or not rows:
        return "<p class='empty'>No rows returned for this item.</p>"
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape('' if r.get(c) is None else str(r.get(c)))}</td>" for c in columns)
        + "</tr>"
        for r in rows[:25]
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def storyboard_html(board: Storyboard, session) -> str:
    blocks: list[str] = []
    for item in sorted(board.items, key=lambda i: i.order):
        body = ""
        if item.spec is not None:
            try:
                cols, rows, _ = chart_data(session.df, item.spec, session.semantics)
                body = _table_html(cols, rows)
            except Exception as exc:
                body = f"<p class='empty'>This item could not be recomputed for export ({html.escape(type(exc).__name__)}).</p>"
            body = (
                f"<p class='meta'>{html.escape(item.spec.chart_type)} &nbsp;·&nbsp; "
                f"{html.escape(item.spec.aggregation.value)}</p>" + body
            )
        if item.text:
            body += f"<p>{html.escape(item.text)}</p>"
        blocks.append(
            f"<section><h2>{html.escape(item.title)}</h2>"
            f"<p class='kind'>{html.escape(item.evidence_kind.value)}</p>"
            f"<p>{html.escape(item.description)}</p>{body}</section>"
        )

    filters = ", ".join(f"{html.escape(f.column)} {f.op} {html.escape(str(f.value))}" for f in board.global_filters)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(board.title)}</title>
<style>
  @page {{ size: A4; margin: 16mm; }}
  body {{ font: 15px/1.55 "Iowan Old Style", "Charter", Georgia, serif; color: #1b1f23; max-width: 900px; margin: 0 auto; padding: 32px 24px; }}
  h1 {{ font-size: 30px; margin: 0 0 4px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 19px; margin: 0 0 6px; }}
  .lede {{ color: #4a5560; margin: 0 0 28px; }}
  section {{ border-top: 1px solid #d8dee4; padding: 22px 0; break-inside: avoid; }}
  .kind {{ display: inline-block; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 11px; letter-spacing: .04em;
           color: #3d4d5c; background: #eef2f5; border-radius: 3px; padding: 2px 7px; margin: 0 0 8px; }}
  .meta {{ font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; color: #66727d; }}
  table {{ border-collapse: collapse; width: 100%; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #e3e8ec; padding: 6px 8px; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  .empty {{ color: #7a8691; font-style: italic; }}
  footer {{ margin-top: 32px; border-top: 2px solid #1b1f23; padding-top: 12px; font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; color: #55606b; }}
</style></head>
<body>
<h1>{html.escape(board.title)}</h1>
<p class="lede">{html.escape(board.description or "Executive dashboard blueprint")}</p>
<p class="meta">Source: {html.escape(session.filename)} &nbsp;·&nbsp; {session.overview.row_count:,} rows &nbsp;·&nbsp;
   {session.overview.column_count} columns &nbsp;·&nbsp; quality {session.overview.quality.score:.0f}
   ({html.escape(session.overview.quality.grade)}){" · sampled" if session.overview.sampled else ""}</p>
{f'<p class="meta">Global filters: {filters}</p>' if filters else ""}
{"".join(blocks) or "<section><p class='empty'>No items have been added to the storyboard yet.</p></section>"}
<footer>Generated by AI BI Analyst on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.
Figures are computed from the uploaded file. Report ideas and narrative are suggestions that require
business validation before publication.</footer>
</body></html>"""
