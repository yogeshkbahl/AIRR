# Change log

## 1.3.0 — vulnerability remediation and a permanent security gate

Every dependency advisory found in 1.2.0 is fixed, and the checks that found
them now run on every push and weekly on a schedule. Nothing was resolved by
lowering a threshold.

### Findings fixed

`npm audit` reported **11 vulnerabilities (3 critical, 6 high, 2 moderate)**:

| Package | Was | Now | Advisory |
| --- | --- | --- | --- |
| plotly.js (via react-plotly.js) | 2.35.2 | 4.1.1 | XSS sanitiser bypass (critical) |
| maplibre-gl (transitive, via plotly) | ≤6.4.0 | patched | XSS sanitiser bypass in `DOM.sanitize()` (critical) |
| vitest / @vitest/mocker | 2.1.4 | 5.0.0 | Path traversal and arbitrary file read via mock redirect (critical) |
| react-router-dom / @remix-run/router | 6.27.0 | 7.18.3 | XSS via open redirect (high) |
| vite / esbuild | 5.4.10 | 7.3.6 | Dev server would answer cross-origin requests and leak responses (high) |
| @playwright/test | 1.48.2 | 1.63.0 | Browser download without SSL certificate verification (high) |

`pip-audit` reported **28 advisories across 3 packages**:

| Package | Was | Now |
| --- | --- | --- |
| starlette (via fastapi) | 0.48.0 | 1.6.0 (fastapi 0.141.1) |
| python-multipart | 0.0.12 | 0.0.32 |
| pyarrow | 18.0.0 | 25.0.1 |

The analysis stack moved with them: pandas 2.3.3, numpy 2.3.4, scipy 1.16.2,
scikit-learn 1.7.2, duckdb 1.4.2, uvicorn 0.53.0, pydantic 2.13.5, httpx 0.28.1.

Verified after every bump: **148 backend tests, 49 frontend tests**, ruff and
ruff format clean, strict TypeScript clean, production build succeeds. The
React Router 7 and Plotly 4 majors needed no source changes.

### Security gate added

- **Bandit rules are now part of lint.** `select` gained `S` in
  `backend/pyproject.toml`, so `ruff check` is a security check rather than a
  style check. Its two findings were resolved: a bare `except: pass` in encoding
  detection now logs, and the one string-built SQL statement carries a
  `# noqa: S608` with the reasoning and a pointer to the injection test beside it.
- **`make audit` and `npm run audit`** run both dependency audits. The npm gate
  is two-stage: any severity fails for runtime dependencies, moderate and above
  for the full tree including build tooling.
- **`make verify`** is the single pre-ship command: lint, tests, audits.
- **`.github/workflows/ci.yml`** runs all of it on push and pull request, plus a
  Trivy scan of the backend image, a gitleaks scan of full history, and CodeQL
  with `security-extended` for both languages. It also runs weekly, because
  advisories appear against code nobody touched.
- **`SECURITY.md`** documents the gate, the application's own threat model with
  the test that enforces each control, and — just as importantly — what is *not*
  covered: no authentication, no rate limiting, no encryption at rest,
  heuristic PII detection.
- Dev tooling is pinned (`pytest`, `ruff`, `pip-audit`) so CI and a laptop agree.

**Files.** `.github/workflows/ci.yml` (new), `SECURITY.md` (new),
`backend/requirements.txt`, `backend/requirements-dev.txt`,
`backend/pyproject.toml`, `backend/app/core/ingestion.py`,
`backend/app/core/query_plan.py`, `frontend/package.json`,
`frontend/package-lock.json`, `Makefile`, `README.md`.

---

## 1.2.0 — governed data quality and dataset preview

After: **148 backend tests, 49 frontend tests, all passing**; ruff check and
ruff format clean; strict TypeScript clean.

Two requirements from the enhanced prompt's feature set that 1.1.0 deferred are
now implemented as complete vertical slices.

### DQ-01 — Governed data-quality rules

The app could already detect anomalies, but an anomaly is a *suspicion*. There
was no way to state a constraint the business actually stands behind, so nothing
could ever be reported as a confirmed breach.

- `core/quality_rules.py` supports six rule kinds: `required`, `unique`,
  `accepted_values`, `numeric_range`, `regex_format` and `date_range`, each with
  a severity and a tolerance (the failure percentage the business accepts before
  it counts as a breach).
- Rules are **proposed** from the profile — a column that is already complete is
  probably mandatory, a near-unique identifier is probably the row key, a
  low-cardinality dimension probably has an agreed member list — and every
  proposal arrives **disabled**. A rule only becomes a rule when a person
  enables it, which is what makes a reported breach defensible.
- Every result carries the exact expression that produced the count, the
  pass/fail row counts, masked example values, the remediation, and
  `evidence_kind: fact`. Statistical suspicions are listed separately in the
  same report, explicitly labelled as inference.
- Rule *definitions* are user configuration and survive a semantic override;
  rule *results* are derived and are invalidated with the rest of the derived
  artifacts.
- Accepted-values comparison is trimmed and case-insensitive on purpose, and the
  expression says so: a casing variant is a consistency anomaly, not a breach of
  the agreed list.
- `GET /quality-report.csv` exports a concise report with failing values omitted
  and formula injection neutralised.

**Files.** `backend/app/core/quality_rules.py` (new), `backend/app/schemas.py`,
`backend/app/core/cache.py`, `backend/app/core/store.py`,
`backend/app/api/v1/routes.py`, `frontend/src/components/QualityRulesPanel.tsx`
(new), `frontend/src/pages/ProfilePage.tsx`, `frontend/src/api/{client,types}.ts`.

**Validation.** `tests/test_quality_and_preview.py` (all six kinds including
inclusivity, tolerance, masked examples, date windows; suggestions are disabled
proposals; validation rejects an empty allowed list, a bad regex, a range with
no bound and an out-of-range tolerance; merge keeps untouched suggestions; the
report separates breaches from suspicions; the CSV export is formula-safe and
leaks no failing values); `tests/test_enhancements.py` (suggested-then-enabled
flow through the API, rules survive an override, invalid rule rejected, rule for
a missing column dropped, CSV export); `src/test/quality.test.tsx` (proposals
switched off, enabling goes through the API, breach shown as a fact with its
expression, suspicions kept separate, passing rules hidden until asked, a
rejected rule surfaces).

### DQ-02 — Dataset preview

- `core/preview.py` serves paginated rows with click-to-sort headers and a
  filter that reuses the same `FilterOp` allow-list as the governed query plan.
  The column must exist, so a preview request cannot become an arbitrary
  expression, and the value is always used as data.
- Sensitive columns are masked in the backend before any row is serialised, and
  filtering *on* a personal-data column is refused rather than quietly allowed.
- Text equality is matched as a case-insensitive contains, which is the useful
  reading for a search box; the response says so instead of leaving the user to
  guess.

**Files.** `backend/app/core/preview.py` (new), `backend/app/schemas.py`,
`backend/app/api/v1/routes.py`, `frontend/src/components/DataPreview.tsx` (new),
`frontend/src/pages/ProfilePage.tsx`.

**Validation.** `tests/test_quality_and_preview.py` (pagination, masking,
sorting, allow-listed filtering, unknown column and unknown sort rejected,
personal-data filter refused, injection string matches nothing, limit capped,
empty page, timestamps and NaN serialised); `tests/test_enhancements.py`
(paginated/sorted/masked through the API, allow-listed filter, bad operator is a
422); `src/test/quality.test.tsx` (masked column shown, personal-data column
absent from the filter list, next page by offset, sort request, empty state).

### Housekeeping

`ruff check` and `ruff format` now pass across the backend (the 1.0.0 baseline
had 97 lint findings that no one had run). Formatting touched files I did not
change functionally; those diffs are cosmetic.

---

## 1.1.0 — enhancement release

Baseline: version 1.0.0 as supplied (66 backend tests, 12 frontend tests, all passing).
After: **115 backend tests, 38 frontend tests, all passing**; strict TypeScript clean.

The six items in the mandatory backlog are implemented against the existing
codebase. No framework migration, no rewrite, no removed features. The public
API kept every 1.0.0 endpoint and response field; new fields are additive.

---

### ENH-01 — Persistent LLM model and token-usage panel

**Root cause.** `llm/providers.py` parsed only the message content and threw away
the provider's `usage` block. There was no ledger, no endpoint and no panel, so
the UI could show only the model the user had *selected*.

**Resolution.**
- Providers now return an `LLMResult` carrying parsed JSON, the provider-reported
  `TokenUsage` and the model string from the response body. OpenAI's
  `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens`
  and Anthropic's `cache_read_input_tokens` / `cache_creation_input_tokens` are
  read straight through. Nothing is estimated.
- `llm/usage.py` holds a per-dataset ledger keyed by a backend request id, so a
  retried or replayed frontend call cannot double count. Failures are recorded
  too, which is what lets the panel show a failure state without losing the
  confirmed total.
- Every model call in `llm/service.py` goes through one `_call` gate that records
  usage on success and failure, and performs at most one controlled JSON repair.
- `GET /datasets/{id}/llm-usage` reports the *resolved* provider and model. Ask
  for OpenAI with no server key and it answers `state: not_configured`, naming
  the narrator that actually replied.
- Usage is persisted as a cache artifact, so it survives a restart or refresh.
- `UsagePanel` is anchored at the bottom of the left navigation on every route,
  with a compact chip in the header bar on small screens. It refetches through
  the API client's activity notifier after each completed request, with no page
  reload and no polling.

**Files.** `backend/app/llm/usage.py` (new), `backend/app/llm/providers.py`,
`backend/app/llm/service.py`, `backend/app/api/v1/routes.py`,
`backend/app/core/store.py`, `frontend/src/components/UsagePanel.tsx` (new),
`frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`.

**Validation.** `tests/test_cache_and_usage.py` (usage extraction for both
providers, dedupe by request id, per-dataset scoping, failure preserves the
total, unconfigured and heuristic states, no secrets in a record);
`tests/test_enhancements.py` (resolved-vs-requested provider, survives a lost
session, no secrets in the response); `src/test/panels.test.tsx` (six panel
states); `e2e/enhancements.spec.ts` (visible on all seven routes).

---

### ENH-02 — Data Profile summary tiles

**Root cause.** The four tiles were built inline inside `OverviewPage`, so Data
Profile had none and the two pages could have drifted apart.

**Resolution.** One `useDatasetSummary` selector and one `DatasetSummaryTiles`
component, both consumed by Overview and Data Profile. The query key is
dataset-scoped and a payload whose `dataset_id` does not match is discarded, so
one dataset's numbers can never appear under another. Tooltips state the
denominator and the rule: duplicates are full-row matches, missing cells are
nulls with blank strings reported separately in the column profile. Loading
shows skeletons; an unavailable summary shows an explanation instead of numbers.

**Files.** `frontend/src/hooks/useDatasetSummary.ts` (new),
`frontend/src/components/DatasetSummaryTiles.tsx` (new),
`frontend/src/pages/OverviewPage.tsx`, `frontend/src/pages/ProfilePage.tsx`.

**Validation.** `src/test/panels.test.tsx` (four numbers with denominators, one
fetch serves both instances, error state renders no numbers, cross-dataset
payload rejected); `tests/test_enhancements.py::test_profile_tiles_come_from_the_same_overview_object`;
`e2e/enhancements.spec.ts` (tile text identical on both pages).

---

### ENH-03 — Chart Advisor selection consistency

**Root cause.** Three separate defects produced the reported mismatch.
1. Advice was fetched with `useMutation`, which has no request identity: a slow
   earlier response resolved after a newer one and overwrote it.
2. The active option was stored as the option *object*, so a card such as
   *Distribution of Cost* survived deselecting `Cost`.
3. The Autocomplete value was rebuilt by filtering the options array, which
   discarded the user's ordering.

**Resolution.**
- One canonical ordered `selected_column_ids`, using stable backend column ids,
  held in dataset-scoped workspace state. Badges, the count in the field label,
  the request payload, the option list, titles, the preview and the tabs all
  derive from it.
- `canonicalSelection` (frontend) and `canonical_selection` (backend) collapse
  duplicates, preserve order, cap at eight and *report* unknown columns rather
  than dropping them silently. A selection of only unknown columns is a typed
  400 rather than a misleading empty result.
- `build_fingerprint` is defined identically on both sides
  (`dataset_id|a>b>c`). The backend echoes `dataset_id`, `selection_fingerprint`
  and `request_id`; the client renders a response only when both match the
  current selection. Advice is a query keyed by the fingerprint, so a superseded
  request is aborted through its `AbortSignal` and a late one cannot be rendered.
- The stored chart type is re-validated against the current options on every
  change and cleared when it is no longer valid.

**Files.** `backend/app/core/chart_rules.py`, `backend/app/schemas.py`,
`backend/app/api/v1/routes.py`, `frontend/src/utils/selection.ts` (new),
`frontend/src/pages/ChartAdvisorPage.tsx`, `frontend/src/api/client.ts`.

**Validation.** `src/test/selection.test.ts` (16 tests: ordering, dedupe,
truncation, fingerprint format parity with the backend, reordering changes
identity, stale and cross-dataset rejection); `tests/test_enhancements.py`
(canonical selection, fingerprint, every option references only selected
columns, unknown columns reported, all-unknown is an error, >8 rejected);
`e2e/enhancements.spec.ts` (selection changes, delayed first response,
no *Distribution of Cost* for an unselected column).

---

### ENH-04 — Ask the Data Quick Ask actions

**Root cause.** `STARTERS` was a hardcoded string array. Suggestions such as
"associated with churn" were offered on datasets with no churn column, the
question text was the only input to the analysis, and a failure left no trace.

**Resolution.**
- `core/quick_asks.py` generates suggestions from the dataset's own semantics.
  Each has a stable id (`qa-<kind>-<hash>`), a category and a structured
  `QuickAskIntent`; a suggestion is only emitted when the columns it needs exist
  with the right analytical role.
- `GET /datasets/{id}/quick-asks` serves them; they are cached as an artifact and
  regenerated after a semantic override.
- `POST /questions` accepts `quick_ask_id` and maps the stored intent to a plan
  deterministically. No visible text is parsed to decide what to run.
- Driver questions run through the association engine; the result's `sql_like`
  says so rather than implying an aggregate query, and the plan carries the
  "association, not cause" assumption.
- The response carries `state` (`answered` / `clarification_required` /
  `empty_result`) and echoes `client_request_id`. The UI renders every state,
  keeps a failed request on screen with a retry action, blocks double
  submissions while one is in flight, and discards an answer whose token is not
  the latest. An unknown id returns a typed, actionable 400.

**Files.** `backend/app/core/quick_asks.py` (new), `backend/app/schemas.py`,
`backend/app/api/v1/routes.py`, `backend/app/core/pipeline.py`,
`frontend/src/utils/askPayload.ts` (new), `frontend/src/pages/AskDataPage.tsx`.

**Validation.** `tests/test_enhancements.py` (generated from the dataset, stable
ids, all six categories complete the flow, driver path uses the engine, unknown
id is actionable, suggestions follow an override);
`src/test/selection.test.ts` (payload contract against `QuestionRequest`);
`e2e/enhancements.spec.ts` (every chip, keyboard activation, injected failure
then retry).

---

### ENH-05 — Chart Advisor state across navigation

**Root cause.** All Chart Advisor state was component-local `useState`, so
unmounting discarded it. `RecommendationsPage` ran `useEffect(() => generate.mutate())`
on mount, which re-ran the whole recommendation pass on every visit. Deleting a
session called `window.location.assign`, a full page load.

**Resolution.**
- `GET|PUT /datasets/{id}/workspace-state` persists durable per-dataset UI state
  (selection, active chart, question, scroll position, recommendation tier,
  profile role filter) as a cache artifact. The server forces the dataset id,
  drops columns that no longer exist, and clears the active chart type when the
  semantic schema changes.
- `useWorkspaceState` keeps it in the query cache with `staleTime: Infinity` and
  a debounced write, so navigating away and back re-reads the cache with no
  refetch and no re-analysis, and a deliberate refresh restores the last saved
  state for that dataset only.
- Recommendations became a cached query keyed by dataset and narrator.
- Session deletion now uses client-side `navigate('/')`.

**Files.** `backend/app/schemas.py`, `backend/app/core/store.py`,
`backend/app/api/v1/routes.py`, `frontend/src/hooks/useWorkspaceState.ts` (new),
`frontend/src/pages/ChartAdvisorPage.tsx`, `frontend/src/pages/RecommendationsPage.tsx`,
`frontend/src/pages/ProfilePage.tsx`, `frontend/src/App.tsx`.

**Validation.** `tests/test_enhancements.py` (roundtrip, drops missing columns,
cannot be written across datasets, survives a lost session);
`e2e/enhancements.spec.ts` (navigate away and back with zero extra advice calls
and zero re-profiling; refresh restores the selection).

---

### ENH-06 — Dataset-scoped cache package

**Root cause.** `store.py` held analysis state in process memory with an
unversioned Parquet dump beside the upload. There was no content fingerprint, no
manifest, no locking, no validation before reuse and no rebuild path, so a
restart lost every session and a corrupt file could break a request.

**Resolution.** `core/cache.py` implements a workspace per dataset:

```
<workspace>/<dataset_id>/
    manifest.json               fingerprint, versions, artifact registry
    original/<sanitised name>   the uploaded bytes, unchanged
    artifacts/<name>.json       one typed artifact per file
    .lock                       advisory lock for concurrent writers
```

- Identity is the opaque dataset id plus a SHA-256 content fingerprint, never the
  filename. The manifest records size, normalised filename, upload timestamp,
  parser settings, profile version, cache-schema version, analysis-code version,
  app version, sampling status, overrides hash and analysis timestamp.
- Writes go to a temp file then `os.replace`, under a cross-platform lock with a
  stale-lock break, so a crash or a concurrent writer cannot leave a partial
  artifact.
- Before reuse, an artifact is checked against the cache-schema version, the
  analysis-code version, a hash of the analysis configuration, the semantic
  overrides hash and its own SHA-256 checksum. A failure invalidates just that
  artifact.
- A corrupt, truncated or tampered artifact is logged, removed and rebuilt on
  the next request instead of raising.
- Artifact names come from an enum and the dataset id is regex-validated, so no
  request can steer a path. Directories are 0o700 and files 0o600.
- Sessions rehydrate from the workspace after a restart: the original file is
  re-read with its recorded parser settings, overrides are re-applied, and
  validated artifacts are reused rather than recomputed.
- `cache hit / miss / rebuild / write / create / delete` are logged with the
  dataset id and reason, never with data values.
- A semantic override invalidates only the derived artifacts; the storyboard,
  workspace state and usage ledger survive.
- Deletion and retention remove the upload and every artifact together.
- `GET /datasets/{id}/cache` and `GET /diagnostics` expose metadata only.

**Files.** `backend/app/core/cache.py` (new), `backend/app/core/store.py`
(rewritten on top of it), `backend/app/core/pipeline.py` (`restore_session`),
`backend/app/api/v1/routes.py`.

**Validation.** `tests/test_cache_and_usage.py` (21 tests: traversal rejection,
same-filename isolation, fingerprint parity, reuse, override invalidation,
version invalidation, corrupt and tampered recovery, no temp-file leakage, eight
concurrent writers, complete deletion, no data in the report);
`tests/test_enhancements.py` (manifest on upload, identical filenames separate,
restore-from-cache after a restart, rebuild after corruption, override keeps the
storyboard, delete removes everything, diagnostics);
`e2e/enhancements.spec.ts` (two same-name uploads, delete removes the cache).

---

### Supporting work

- **Provider resilience.** One shared transport with a timeout, retry with
  backoff on 408/409/425/429/5xx, shaped rate-limit messages, and a `retryable`
  flag on `LLMError`.
- **Structured-output repair.** One controlled repair attempt on malformed JSON,
  then a useful error, as required by the structured-LLM contract.
- **Operational diagnostics.** `GET /api/v1/diagnostics` covers API version,
  storage writability and size, available parsers, cache versions and configured
  providers, with no secrets.
- **Semantic-override correctness.** Overrides now invalidate exactly the derived
  artifacts and regenerate Quick Asks, so a role change cannot leave a stale
  suggestion pointing at a column that is no longer a measure.

### Not included

The wider "enhanced feature set" wish-list beyond the mandatory six is
deliberately untouched: the configurable data-quality rules engine, dataset
preview with pagination, period-over-period comparison, cross-filtering, chart
image export and saved-analysis versioning. They are recorded in
`docs/ROADMAP.md` rather than half-built.
