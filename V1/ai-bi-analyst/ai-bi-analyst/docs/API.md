# API reference

Base path `/api/v1`. Interactive docs at `http://localhost:8000/docs`.

Every response carries an `x-request-id` header. Errors share one shape:

```json
{ "error": "dataset_not_found", "detail": "The analysis session has expired or was deleted. Upload the file again.", "request_id": "9f2c1ab34d55" }
```

Error codes: `invalid_file`, `ingestion_failed`, `dataset_not_found`, `anomaly_not_found`,
`unknown_column`, `invalid_chart_spec`, `chart_data_failed`, `invalid_plan`, `validation_failed`,
`internal_error`.

## Meta

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/health` | Status, configured providers (never keys), and the active limits |
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

## Relationships

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/relationships` | Body: `{columns?: string[]}`. Symmetric matrix plus ranked pairs and the count of suppressed pairs |
| `POST` | `/datasets/{id}/relationships/pair` | Body: `{column_x, column_y, method?, segment_by?}`. Statistics, a suitable chart spec and the segment breakdown |

## Recommendations and charts

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/recommendations` | Body: `{provider?, count?}`. Validated, tier-grouped recommendations with provenance and rejection notes |
| `POST` | `/datasets/{id}/chart-advice?provider=` | Body: `{columns[], question?, use_llm}`. Compatible options with specs, plus rejected options with blockers |
| `POST` | `/datasets/{id}/chart-data` | Body: a `ChartSpec`. Validated (and repaired if possible), then executed. `400` if it cannot be made valid |

## Questions and plans

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/datasets/{id}/questions?provider=` | Body: `{question, columns[], use_llm}`. Returns the plan, the executed result, the compiled query, a chart spec and the executive answer |
| `POST` | `/datasets/{id}/plan` | Body: an `AnalysisPlan`. Runs a plan directly, for a client that builds its own |

## Storyboard

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/datasets/{id}/storyboard` | Current storyboard plus the executive-page completeness checklist |
| `PUT` | `/datasets/{id}/storyboard` | Validates every item's chart spec before saving. `400` names the offending item |
| `GET` | `/datasets/{id}/storyboard/export.json` | Definition, semantic layer and governance note, as a download |
| `GET` | `/datasets/{id}/storyboard/export.html` | Printable A4 view with every item recomputed |
