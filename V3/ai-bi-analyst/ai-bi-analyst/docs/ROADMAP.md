# Roadmap

Ordered by what a team hits first when it tries to put this in front of real users.

## 1. Authentication and tenancy
OIDC/SAML sign-in, workspace-scoped datasets, and a `user_id` + `tenant_id` on every session and
audit row. `core/store.py` is the only module that needs to learn about ownership.

## 2. Durable, multi-node state
Version 1.1.0 made a dataset survive a restart: each upload has a fingerprinted workspace with a
versioned manifest and atomically written artifacts, and a session rehydrates from it. The remaining
work is to move that workspace to object storage (S3/GCS/Azure), the dataset index and audit trail to
PostgreSQL, the file lock to a distributed lock, and profiling and LLM calls to a queue (Celery/RQ/Arq).
The `status` endpoint and progress states already exist for that; today the work happens inline.

## 2a. Deferred UI and analysis features
The rules engine and the dataset preview shipped in 1.2.0. Still recorded here rather than
half-built: period-over-period change when a valid
date grain and comparison period exist; global dashboard filters with cross-filtering that cannot
silently change aggregation grain; chart image export; and versioned saved chart specifications so an
older session degrades gracefully after a schema change.

## 3. Enterprise data sources
Read directly from Oracle, Snowflake, BigQuery, Postgres and SQL Server instead of a file: a
connection registry, schema browsing, and pushdown of the same `AnalysisPlan` to the source dialect
rather than DuckDB. The plan compiler is already the single place SQL is produced.

## 4. Oracle OAS/OBIEE integration
Import subject areas so the semantic layer starts governed rather than inferred, then generate catalog
analyses and dashboard definitions from a storyboard. Read RPD logical columns, hierarchies and
aggregation rules and reconcile them against the inferred roles, flagging disagreements as findings.

## 5. Scheduled refresh and monitoring
Re-profile a source on a schedule, diff the profile against the last run, and alert on drift: a new
column, a changed role, a jump in missingness, a quality-score drop, or a metric outside its band.
The anomaly timeline chart is the natural front end for this.

## 6. Persistent semantic model
Promote per-session overrides *and data-quality rules* into a reusable model: named metrics with
formulas and owners, certified hierarchies, business glossary terms, row-level filters, and a rule set
that follows a source across uploads instead of living in one dataset workspace. Once metrics are certified, the
narrator stops guessing definitions and starts citing them.

## 7. Role-based access and certification
Roles for viewer, analyst, steward and admin. A certification workflow where a steward signs off a
metric or a storyboard, after which the UI marks it certified and the export carries the signature.

## 8. Analytical depth
Proper cohort and funnel engines behind their chart specs, driver analysis with permutation importance
and SHAP-style explanations, forecasting with backtests and error bands, and automatic segment
discovery for variance explanation. Each is already specified as a chart type with prerequisites, so
the work is implementation rather than design.
