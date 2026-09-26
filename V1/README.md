# AI BI Analyst

A governed analytical workbench. You upload a data file and a sentence of business context; the app
profiles the data in Python, tells you what is wrong with it, shows which columns actually relate to
each other, proposes report ideas grouped by how hard they are to build, answers questions by running
real queries, and assembles an executive dashboard blueprint you can export.

**The rule the whole codebase is built around:** Python calculates every number, and the language
model only interprets numbers that have already been calculated. The model never invents a column, a
value, an aggregation or a query result. It is also optional — the app ships with a rule-based
narrator and runs with no API key at all.

---

## Quick start

Two terminals, no Docker, no API key.

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python samples/generate_samples.py                    # writes two synthetic files with known defects
uvicorn app.main:app --reload --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173> and upload `backend/samples/retail_orders.csv`.

With Docker instead:

```bash
cp .env.example .env
docker compose up --build     # UI on :5173, API on :8000, docs on :8000/docs
```

`make help` lists every task (`make backend`, `make frontend`, `make test`, `make e2e`, `make samples`).

### Turning on a real model (optional)

```bash
cp .env.example .env
# then set one of these in .env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# or
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart the backend. The narrator dropdown in the top right switches per request; a provider without a
server-side key is shown as unavailable and cannot be selected. The browser never receives the key.

---

## What the app does, page by page

| Page | What it gives you |
| --- | --- |
| **Upload & context** | Drop zone with content sniffing, Excel sheet picker, business context box, and a plain statement of what is sent to the model |
| **Overview** | Exact record/column counts, role breakdown, duplicates, missing cells, and a quality score with every component and formula shown |
| **Data profile** | Searchable column table with numeric/categorical/date statistics, editable roles and aggregations, and findings you can drill into row by row |
| **Relationships** | Association matrix that picks its statistic from the pair of roles, pair drill-down with effect size and p-value, and a Simpson's paradox check |
| **Report ideas** | Recommendations grouped into Easy / Medium / Complex / Very complex, each with audience, metrics, drill path, cautions and provenance |
| **Chart advisor** | Multi-select columns, see exactly which charts are valid, which are not and why, with a live preview from real data |
| **Ask the data** | Question → readable analysis plan → parameterised query → executive answer with evidence and caveats, plus the SQL that ran |
| **Storyboard** | Pin charts and findings, reorder them, check them against an executive-page checklist, export as JSON or a printable HTML page |

Every statement in the UI is labelled with its evidence class, because the difference matters:

- **Fact** — calculated from your file.
- **Inference** — a statistical reading of those calculations.
- **Suggestion** — a reporting idea from the narrator.
- **Assumption** — depends on business knowledge the tool does not have. Confirm before acting.

---

## Architecture

```
                        browser (React, TS, MUI, Plotly)
                                     │  /api/v1  (no keys, no secrets)
                                     ▼
   ┌──────────────────────── FastAPI ────────────────────────┐
   │ ingestion   sniff content, encoding, delimiter, sheets  │
   │ semantics   physical type + analytical role + agg rule  │
   │ profiling   overview, column stats, quality score       │
   │ anomalies   19 checks, each with method and next step   │
   │ relationships  type-aware association engine (SciPy)    │
   │ chart_rules    deterministic compatibility + repair     │
   │ query_plan     allow-listed plan → parameterised DuckDB │
   │ export         storyboard JSON + printable HTML         │
   └───────────────────────────┬────────────────────────────┘
                               │ compact profile only
                               ▼
                  LLM service (OpenAI | Anthropic | built-in)
                  structured JSON → Pydantic → validated
                  against real columns → repaired or rejected
```

The full pipeline, module by module, is in [ARCHITECTURE.md](ARCHITECTURE.md). The endpoint list is in
[docs/API.md](docs/API.md) and the versioned prompts are in [docs/PROMPTS.md](docs/PROMPTS.md).

### Repository layout

```
ai-bi-analyst/
├── backend/
│   ├── app/
│   │   ├── main.py                 app, middleware, typed error handlers
│   │   ├── config.py               env-driven settings and thresholds
│   │   ├── schemas.py              every API and LLM contract
│   │   ├── api/v1/routes.py        versioned endpoints
│   │   ├── core/
│   │   │   ├── ingestion.py        content sniffing, encoding, delimiters, limits
│   │   │   ├── semantics.py        roles, aggregation defaults, hierarchies
│   │   │   ├── sensitive.py        PII detection and masking
│   │   │   ├── profiling.py        statistics and the explainable quality score
│   │   │   ├── anomalies.py        findings with method, scope, action
│   │   │   ├── relationships.py    Pearson/Spearman/Cramér's V/eta/point-biserial
│   │   │   ├── chart_rules.py      chart catalog, compatibility, validation, repair
│   │   │   ├── query_plan.py       governed plan compiler and executor
│   │   │   ├── pipeline.py         orchestration
│   │   │   ├── store.py            session workspace, SQLite metadata, audit
│   │   │   └── export.py           storyboard exports
│   │   └── llm/
│   │       ├── providers.py        one interface, three implementations
│   │       ├── prompts.py          versioned prompt catalog
│   │       └── service.py          validation, repair, deterministic fallback
│   ├── samples/generate_samples.py synthetic data with deliberate defects
│   └── tests/                      66 tests
└── frontend/
    ├── src/api/                    typed client mirroring the Pydantic models
    ├── src/components/             evidence tags, chart renderer, chart card
    ├── src/pages/                  the eight routes
    ├── src/hooks/useStoryboard.ts  storyboard state via the API
    └── e2e/workflow.spec.ts        critical-path Playwright test
```

---

## How the governance actually works

**Semantic typing that respects meaning.** A numeric column named `postal_code`, `customer_id` or
`phone` is never treated as a measure. Rate columns default to average, not sum. Low-cardinality
integers are treated as categories. Every decision carries a reason string, and you can override any
of it — the profile, findings and relationships all recompute.

**Type-aware association.** Pearson is not sprayed across every pair:

| Pair | Method |
| --- | --- |
| numeric × numeric | Pearson, with Spearman alongside to expose non-linearity |
| categorical × categorical | bias-corrected Cramér's V with chi-square p |
| categorical × numeric | correlation ratio (eta) with one-way ANOVA |
| boolean × numeric | point-biserial |
| date × numeric | Spearman against time plus a monthly OLS slope |

Results are suppressed for tiny samples, constant columns, unsupported roles and excessive
missingness, and every reading ends with the reminder that association is not causation.

**Chart compatibility is decided by code, not by the model.** Each of the 26 chart types in the
catalog has prerequisites, avoid-when conditions and a check function. The backend computes which are
valid for your selection; only then is the model asked to rank the valid ones. A model recommendation
referring to a column that does not exist, or summing an identifier, is repaired if possible and
rejected otherwise — and the UI shows you both counts.

**No generated SQL or Python is ever executed.** Questions become an `AnalysisPlan` (metrics,
dimensions, time grain, filters, limit). The plan is validated against the real column list, then
compiled to DuckDB SQL using an allow-list of aggregate functions, with quoted identifiers, bound
parameters, a row cap and a wall-clock timeout. The UI shows you the plan and the compiled query.

---

## Security and privacy

- Provider keys are read from backend environment variables only. They are never returned by an
  endpoint, never logged, and never present in frontend state.
- Uploads are identified by content, not by extension. Legacy/encrypted Office files, PDFs and binary
  payloads are refused with an actionable message. Filenames are sanitised; macros are never executed.
- Columns that look like email, phone, government ID, account, card or credential data are detected,
  flagged, masked in sample values, excluded from row previews, and sent to the model as a name and a
  role only.
- Only a compact profile leaves the backend: names, labels, roles, summary statistics, cardinality,
  missingness, anomaly counts, strongest associations and your context text. Raw rows are never sent.
  A masked sample can be opted into via `ALLOW_MASKED_SAMPLE_TO_LLM`, off by default.
- All file content is treated as untrusted data, wrapped in delimiters and explicitly labelled as
  non-instruction in the prompt, which is the defence against prompt injection hidden in a spreadsheet.
- Exported CSV values with a leading `=`, `+`, `-` or `@` are neutralised; exported HTML is escaped.
- Every session is isolated by dataset id, can be deleted from the UI, and is purged after
  `SESSION_RETENTION_HOURS`. An audit row records time, provider, model, prompt version, profile
  version, sampling status and user overrides for each analysis action.

---

## Tests

```bash
cd backend  && pytest                 # 66 tests: unit + API integration
cd frontend && npm run test           # 12 tests: chart mapping, evidence labelling, formatting
cd frontend && npm run typecheck      # strict TypeScript
cd frontend && npm run e2e            # Playwright critical path (needs both servers)
```

The backend suite asserts the things that would quietly ruin a report: that counts are exact and
reproducible, that postal codes and foreign keys are not measures, that rates are never summed, that
SQL injection in a column name or a filter value is inert, that model output naming a non-existent
column is rejected, that PII is absent from the model payload, and that a crafted Simpson's paradox is
caught.

---

## Customising it

| You want to | Change this |
| --- | --- |
| Add a chart type | `backend/app/core/chart_rules.py` (add a `ChartRule` + builder), then a case in `frontend/src/components/ChartRenderer.tsx` |
| Change a detection threshold | `backend/app/config.py` — IQR multiplier, robust z, rare-category rate, cardinality ratio, minimum pair sample |
| Change the quality formula | `quality_score()` in `backend/app/core/profiling.py`; the UI renders whatever components you define |
| Add an anomaly check | `backend/app/core/anomalies.py`, plus a filter branch in `affected_rows_preview` if it needs a row preview |
| Reword or re-version a prompt | `backend/app/llm/prompts.py` — bump the version and it flows into the audit trail |
| Add a provider | Implement `LLMProvider` in `backend/app/llm/providers.py` and add it to `get_provider` |
| Restyle the UI | `frontend/src/theme.ts` holds the whole palette and type scale |
| Add a business rule | Encode it as an anomaly check or a semantic override so it is auditable, rather than putting it in a prompt |

---

## Limitations, stated plainly

- **MVP persistence.** Analysis state lives in the backend process, with Parquet and SQLite on disk.
  Restarting the API clears live sessions. `core/store.py` is the seam for object storage and
  PostgreSQL.
- **Single node.** Profiling runs inline in the request. Files beyond a few hundred MB should move to
  a background worker; the status endpoint and progress states are already in place for that.
- **Sampling.** Above `PROFILE_ROW_LIMIT` rows, column statistics come from a systematic sample. Row
  and null counts remain exact, and everything sampled is labelled as such.
- **No authentication.** There is no login, no tenancy and no role-based access. Do not expose this
  to the internet as-is.
- **Cohort, funnel, decomposition, driver and map views** are proposed with their prerequisites and
  rendered as validated result tables rather than bespoke visuals. The specs are complete enough to
  build them in your BI tool; implementing each visual is deliberately left as the customisation point.
- **No Oracle deployment.** The storyboard exports a definition, not a catalog object or an RPD.

---

## Sample workflow

1. `python samples/generate_samples.py`, then upload `retail_orders.csv` with the context
   "Monthly order extract from the ERP. Regional directors review revenue and margin by channel."
2. **Overview** shows 4,045 rows, 20 columns and a quality score around 92 (B), with the score broken
   into completeness, row uniqueness, type usability, value consistency, numeric validity and open
   findings, each with its formula.
3. **Data profile** finds the seeded defects: 45 duplicate rows, `North` vs `north ` casing variants,
   twelve dates in 2031, negative `units_sold`, extreme `revenue_amount` outliers, a constant
   `currency_code`, and `customer_email` flagged and masked as personal data. Each finding names its
   method and the decision it needs. Click through to the affected rows — without the email column.
4. **Relationships** shows Pearson between the measures, eta between region and revenue, and a trend
   against `order_date`. Segment the strongest pair by `product_category` to check for a reversal.
5. **Report ideas** proposes a revenue KPI, a trend, a ranked comparison and a detail table in the
   easy tier, then heatmaps and scatters in medium, a waterfall and small multiples in complex, and a
   driver view in very complex — each with metrics, drill path and cautions.
6. **Chart advisor**: select `revenue_amount` and `region` and the scatter option disappears with the
   reason "Two measures are needed". Add `order_date` and the trend options appear. Pin one.
7. **Ask the data**: "What is total revenue by region?" returns a plan you can read, a result table, a
   chart, an executive answer with computed evidence, and the exact query that ran.
8. **Storyboard**: the checklist tells you what an executive page is still missing. Export the JSON
   definition for your BI developer, or open the printable view for the review pack.

---

## Roadmap

Authentication and tenancy, enterprise sources, Oracle OAS/OBIEE integration, scheduled refresh, a
persistent semantic model and role-based access are sketched in [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Licence and expectations

Use it, fork it, rename it. It is a starting point for a team that has to defend its numbers — not a
replacement for data governance or domain expertise. Every recommendation it makes needs business
validation before anyone publishes it.
