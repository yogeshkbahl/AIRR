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
| 9. Answer questions | `core/query_plan.py`, `core/quick_asks.py` | Allow-listed plan compiled to parameterised DuckDB SQL with a row cap and timeout. The model sees results, never writes the query. A Quick Ask carries a structured intent, so its plan needs no interpretation at all |
| 10. Record the cost | `llm/usage.py` | Provider-reported tokens only, deduped by backend request id, attributed to one dataset session |
| 11. Persist and validate | `core/cache.py` | Every artifact is written atomically and checked against the content fingerprint and four version hashes before reuse |

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

**`core/cache.py`** — the dataset workspace. Identity is an opaque dataset id plus a SHA-256 content
fingerprint, so two uploads with the same filename can never share state. A `manifest.json` records
the fingerprint, size, normalised filename, parser settings, profile version, cache-schema version,
analysis-code version, app version, sampling status and an artifact registry with a checksum per
artifact. Writes are a temp file plus `os.replace` under a cross-platform advisory lock, which is what
makes a crash mid-write or a concurrent writer safe. `read_artifact` validates the cache-schema
version, the analysis-code version, a hash of the analysis configuration, the semantic-override hash
and the payload checksum before returning anything, and a failure on any of those invalidates just
that artifact. Artifact filenames come from an enum and the dataset id is regex-validated, so a
request cannot steer a path.

**`core/store.py`** — sessions as an in-memory view over a workspace, plus SQLite for the dataset
index and the audit trail. `rehydrate` rebuilds a session after a restart: the original file is
re-read with its recorded parser settings, semantic overrides are re-applied, and validated artifacts
are reused instead of recomputed. Column semantics are always recomputed because they carry parsed
datetime and numeric views the query engine needs, which are not worth serialising. This is still the
single seam to replace for object storage plus PostgreSQL.

**`llm/usage.py`** — a per-dataset ledger keyed by a backend request id. Counts come only from the
provider response; nothing is estimated, because an estimate sitting next to a real figure is worse
than no figure. Failures are recorded as well as successes, which is what lets the panel show a
failure state while keeping the last confirmed total. The summary reports the *resolved* provider and
model, so requesting a provider with no server-side key produces a `not_configured` state naming the
narrator that actually replied.

**`core/quality_rules.py`** — the module that exists to keep two different claims apart. An anomaly is
a statistical suspicion; a rule violation is a confirmed breach of a constraint a human agreed to. So
every result here is tagged `fact`, states the expression it evaluated, and names the remediation.
Rules are proposed from the profile but always arrive disabled, because a breach is only defensible
if someone enabled the rule. Definitions are user configuration and survive a semantic override;
results are derived and are invalidated with the rest.

**`core/preview.py`** — paginated rows with masking applied before serialisation. The filter reuses
the governed `FilterOp` allow-list and requires a real column, and filtering on a personal-data
column is refused rather than silently permitted.

**`core/quick_asks.py`** — suggestions derived from the dataset's semantics, each with a stable id and
a structured intent that maps deterministically to a plan. Driver questions are answered by the
association engine rather than an aggregate query, and the result says so in place of SQL.

## Frontend structure

`src/api/types.ts` mirrors `schemas.py` one-to-one; when a contract changes, both files change
together. `src/api/client.ts` is the only place `fetch` is called, and it turns backend error bodies
into an `ApiError` carrying the code and the request id, so the UI can show the id in a support
message. Server state is TanStack Query; there is no client-side store, and the storyboard, workspace
state and usage ledger all live on the server so a refresh loses nothing.

Three pieces of shared identity keep the UI honest. `utils/selection.ts` defines the canonical
selection and a fingerprint string built exactly as `chart_rules.build_fingerprint` builds it, which
is why a chart-advice response can be matched to the selection that produced it without hashing.
`hooks/useDatasetSummary.ts` is the one selector behind the four summary tiles, so Overview and Data
Profile cannot disagree. `hooks/useWorkspaceState.ts` holds durable per-dataset UI state in the query
cache with `staleTime: Infinity` and a debounced write, so navigating away and back is a cache read
rather than a re-analysis.

`client.ts` also exposes an activity notifier: any call that could reach a provider announces itself
when it settles, and the usage panel invalidates its query in response. That is why the panel updates
after every request without polling and without a page reload.

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
