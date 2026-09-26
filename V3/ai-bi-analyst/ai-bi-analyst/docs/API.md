# API reference

Base path `/api/v1`. Interactive docs at `http://localhost:8000/docs`.

Every response carries an `x-request-id` header. Errors share one shape:

```json
{ "error": "dataset_not_found", "detail": "The analysis session has expired or was deleted. Upload the file again.", "request_id": "9f2c1ab34d55" }
```

Error codes: `invalid_file`, `ingestion_failed`, `dataset_not_found`, `anomaly_not_found`,
`unknown_column`, `unknown_quick_ask`, `invalid_chart_spec`, `chart_data_failed`, `invalid_plan`,
`invalid_quality_rule`, `invalid_preview_request`, `validation_failed`, `internal_error`.

## Meta

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/health` | Status, app version, configured providers (never keys), and the active limits |
| `GET` | `/diagnostics` | Readiness detail: API version, storage writability and size, available parsers, cache versions, configured providers. No secrets, no data values |
| `GET` | `/datasets` | Recent sessions from the metadata database |
| `POST` | `/datasets/probe-sheets` | Multipart file. Returns Excel sheet names so the user can choose before profiling |

## Session lifecycle

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets` | Multipart: `file`, `business_context`, optional `sheet`. Ingests, profiles and returns the overview |
| `GET` | `/datasets/{id}/status` | `pending` / `profiling` / `ready` / `failed` with progress |
| `GET` | `/datasets/{id}/overview` | Counts, role breakdown, duplicates, missingness, quality score with components |
| `POST` | `/datasets/{id}/summary?provider=` | Business interpretation grounded in the computed profile |
| `DELETE` | `/datasets/{id}` | Deletes the session, its files, its audit rows and its storyboard |
| `GET` | `/datasets/{id}/audit` | Audit trail: time, action, provider, model, prompt version, profile version, sampling |

## Profile and findings

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/datasets/{id}/columns` | Full column profiles |
| `PUT` | `/datasets/{id}/columns` | Body: `[{name, analytical_role?, default_aggregation?, label?}]`. Re-runs typing, profiling and anomaly detection |
| `GET` | `/datasets/{id}/anomalies?severity=` | Findings, highest severity first |
| `GET` | `/datasets/{id}/anomalies/{anomaly_id}/rows` | Affected-row preview with sensitive columns removed and listed in `hidden_columns` |

## Preview and data-quality rules

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/preview` | Body: `{offset, limit, sort_by?, sort_desc?, filter_column?, filter_op?, filter_value?}`. Paginated rows with sensitive columns masked server-side. The filter operator comes from the governed allow-list, the column must exist, and filtering on a personal-data column is a `400` |
| `GET` | `/datasets/{id}/quality-rules` | Active rules, seeded with disabled proposals derived from the profile |
| `PUT` | `/datasets/{id}/quality-rules` | Body: `[QualityRule]`. Merges with the stored set; an unusable rule (empty allowed list, bad regex, range with no bound, tolerance outside 0-100) is a typed `400`; a rule for a column that no longer exists is dropped into `dropped_on_restore` |
| `POST` | `/datasets/{id}/quality-rules/evaluate` | Runs the enabled rules. Each result carries the expression evaluated, pass/fail counts, masked examples, remediation and `evidence_kind: fact`, with statistical suspicions listed separately |
| `GET` | `/datasets/{id}/quality-report.csv` | Concise report as a download. Failing values are omitted and leading `=`, `+`, `-`, `@` are neutralised |

## Relationships

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/relationships` | Body: `{columns?: string[]}`. Symmetric matrix plus ranked pairs and the count of suppressed pairs |
| `POST` | `/datasets/{id}/relationships/pair` | Body: `{column_x, column_y, method?, segment_by?}`. Statistics, a suitable chart spec and the segment breakdown |

## Recommendations and charts

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/recommendations` | Body: `{provider?, count?}`. Validated, tier-grouped recommendations with provenance and rejection notes |
| `POST` | `/datasets/{id}/chart-advice?provider=` | Body: `{columns[], question?, use_llm, selection_fingerprint?}`. Returns compatible options with specs and rejected options with blockers, plus `dataset_id`, `selection_fingerprint`, `request_id`, `dropped_columns` and `truncated_selection` so the client can discard a superseded response. Columns are resolved to stable ids in the requested order with duplicates collapsed; a selection of only unknown columns is a `400` |
| `POST` | `/datasets/{id}/chart-data` | Body: a `ChartSpec`. Validated (and repaired if possible), then executed. `400` if it cannot be made valid |

## Questions and plans

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/datasets/{id}/quick-asks` | Suggestions generated from this dataset's schema and roles: stable `id`, `category`, structured `intent` and `required_columns`, plus a `schema_fingerprint` |
| `POST` | `/datasets/{id}/questions?provider=` | Body: `{question, columns[], use_llm, quick_ask_id?, client_request_id?}`. Returns the plan, the executed result, the compiled query, a chart spec, the executive answer, a `state` of `answered` / `clarification_required` / `empty_result`, and the echoed `client_request_id`. With `quick_ask_id` the server runs the stored structured intent instead of interpreting the text; an unknown id is a typed `400` |
| `POST` | `/datasets/{id}/plan` | Body: an `AnalysisPlan`. Runs a plan directly, for a client that builds its own |

## Usage, workspace state and cache

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/datasets/{id}/llm-usage?provider=` | Session token usage: requested vs resolved provider, resolved model, last request, totals, recent records, and a `state` of `not_configured` / `not_used` / `ok` / `error`. Counts are provider-reported and deduped by backend request id. Never contains keys, prompts or data values |
| `GET` | `/datasets/{id}/workspace-state` | Durable per-dataset UI state, pruned against the live semantic schema |
| `PUT` | `/datasets/{id}/workspace-state` | Saves it. The dataset id is forced from the path, columns that no longer exist are dropped into `dropped_on_restore`, and an invalid chart type is cleared |
| `GET` | `/datasets/{id}/cache` | Cache metadata for this dataset: fingerprint prefix, versions, artifact names, disk size, and whether the live session was computed, restored from cache or rebuilt |

## Storyboard

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/datasets/{id}/storyboard` | Current storyboard plus the executive-page completeness checklist |
| `PUT` | `/datasets/{id}/storyboard` | Validates every item's chart spec before saving. `400` names the offending item |
| `GET` | `/datasets/{id}/storyboard/export.json` | Definition, semantic layer and governance note, as a download |
| `GET` | `/datasets/{id}/storyboard/export.html` | Printable A4 view with every item recomputed |
