# Architecture notes

## The non-negotiable pipeline

Every request follows the same order, and no step is allowed to skip ahead.

| Step | Module | Guarantee |
| --- | --- | --- |
| 1. Ingest and validate | `core/ingestion.py` | Format identified from magic bytes, encoding and delimiter detected, size and emptiness checked, filename sanitised, macros never executed |
| 2. Infer types and roles | `core/semantics.py` | Physical type from the data, analytical role from shape plus name evidence, default aggregation per role, hierarchy candidates, reason string for every decision |
| 3. Deterministic profiling | `core/profiling.py` | Overview counts, per-column numeric/categorical/date statistics, weighted quality score with visible components |
| 4. Compact privacy-aware profile | `llm/service.py:compact_profile` | Names, labels, roles, safe summary statistics, cardinality, missingness, anomaly counts, top associations. No raw rows. Sensitive columns reduced to a name and a flag |
| 5. Model call | `llm/providers.py` | Provider-neutral, server-side keys, low temperature, prompt id and version recorded |
| 6. Structured output | `schemas.py` | JSON parsed then validated against Pydantic. Malformed output is a rejection, not a warning |
| 7. Validate against reality | `core/chart_rules.py`, `llm/service.py` | Columns must exist, aggregations must suit the role, chart constraints must hold. Near misses are repaired and the repair is shown to the user |
| 8. Render | `frontend ChartRenderer` | Charts are drawn only from validated specs and backend-computed rows |
| 9. Answer questions | `core/query_plan.py` | Allow-listed plan compiled to parameterised DuckDB SQL with a row cap and timeout. The model sees results, never writes the query |

## Module responsibilities

**`config.py`** — every threshold that affects a number on screen lives here: IQR multiplier, robust
z cut-off, rare-category rate, high-cardinality ratio, near-constant share, minimum pair sample,
row and time limits, retention. Changing a threshold changes the explanation text too, because the
explanations are generated from the settings.

**`core/semantics.py`** — the part that decides whether your report will be right. Name patterns
protect against the classic errors: `ID_NAME_RE` keeps foreign keys out of the measure pool,
`NON_MEASURE_NAME_RE` catches postal codes and phone numbers, `PERCENT_NAME_RE` forces average on
rates, `AVERAGE_NAME_RE` catches scores and per-unit prices, `CURRENCY_NAME_RE` marks money.
Everything is overridable through `PUT /columns`, which re-runs steps 2 and 3.

**`core/anomalies.py`** — nineteen checks across dataset, column and cross-column scope. Each
`Anomaly` carries severity, affected rows and percentage, the method in words, a plain-language
explanation, a recommended action, an evidence class, and an optional row filter so the UI can show
the affected rows with sensitive columns removed. Checks that depend on business meaning (negative
quantities, future dates, percentage ranges) are emitted as `assumption`, never as `fact`.

**`core/relationships.py`** — `pair_kind()` resolves the pair of analytical roles, and the method
follows from it. `cramers_v` is bias-corrected; `correlation_ratio` returns eta with an ANOVA p-value;
`segment_check` recomputes the pair inside each segment and flags a sign reversal as a suspected
Simpson's paradox, which is the difference between a defensible finding and an embarrassing one.

**`core/chart_rules.py`** — a `Selection` describes chosen columns in analytical terms (measures,
categories, dates, geographies, booleans, low-cardinality categories). Each `ChartRule` pairs a
`check(selection) -> blockers` function with a `build(selection) -> ChartSpec` builder, plus
prerequisites and avoid-when text that the UI shows verbatim. `validate_chart_spec` and
`repair_chart_spec` are the gate every model-authored or user-supplied spec passes through.

**`core/query_plan.py`** — `validate_plan` resolves and role-checks every column, downgrades illegal
aggregations, and clamps the limit. `compile_plan` builds SQL from quoted identifiers and an
aggregate allow-list, binding all filter values as parameters. `_run_sql` registers the frame in an
in-process DuckDB connection and arms a timer that interrupts the query at the timeout. A heuristic
natural-language planner handles column matching (aliases, suffix stripping, crude singularisation)
so the app answers questions without a model.

**`llm/service.py`** — the only place model output becomes product behaviour. Recommendations are
validated one by one; a bad one is rejected with a note, a fixable one is repaired with a note, and
if nothing survives the deterministic narrator produces tier-balanced recommendations from the chart
rules instead. The same fallback covers plans, answers and summaries, which is why the product is
fully functional with `LLM_PROVIDER=heuristic`.

**`core/store.py`** — sessions in memory, Parquet snapshot and SQLite metadata on disk, audit rows,
storyboards, column overrides, retention purge on startup. This is the single seam to replace for
object storage plus PostgreSQL; nothing else in the codebase touches persistence.

## Frontend structure

`src/api/types.ts` mirrors `schemas.py` one-to-one; when a contract changes, both files change
together. `src/api/client.ts` is the only place `fetch` is called, and it turns backend error bodies
into an `ApiError` carrying the code and the request id, so the UI can show the id in a support
message. Server state is TanStack Query; there is no client-side store, and the storyboard lives on
the server so a refresh loses nothing.

`ChartRenderer.buildFigure` is a pure function from `(spec, columns, rows)` to a Plotly figure, which
is why it is unit-testable without a DOM. Unsupported chart types return `null` and the component
falls back to the validated result table with an explanation, rather than rendering something
misleading.

## Design intent

The visual language is an instrument panel, not a marketing page: cool graphite paper, one
institutional blue for navigation and action, and a small fixed signal palette reserved for severity
and evidence class so that colour always carries meaning. Figures are set in IBM Plex Mono with
tabular figures so columns of numbers align. Severity uses fill as well as hue, and every status has
a text label, so nothing depends on colour alone. The left rail is numbered because the workflow
genuinely is a sequence.
