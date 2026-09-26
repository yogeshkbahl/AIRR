# Roadmap

Ordered by what a team hits first when it tries to put this in front of real users.

## 1. Authentication and tenancy
OIDC/SAML sign-in, workspace-scoped datasets, and a `user_id` + `tenant_id` on every session and
audit row. `core/store.py` is the only module that needs to learn about ownership.

## 2. Durable, multi-node state
Uploads to object storage (S3/GCS/Azure), metadata and storyboards to PostgreSQL, profiling and LLM
calls to a queue (Celery/RQ/Arq). The `status` endpoint and progress states already exist for this;
today the work happens inline.

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
Promote per-session overrides into a reusable model: named metrics with formulas and owners,
certified hierarchies, business glossary terms, and row-level filters. Once metrics are certified, the
narrator stops guessing definitions and starts citing them.

## 7. Role-based access and certification
Roles for viewer, analyst, steward and admin. A certification workflow where a steward signs off a
metric or a storyboard, after which the UI marks it certified and the export carries the signature.

## 8. Analytical depth
Proper cohort and funnel engines behind their chart specs, driver analysis with permutation importance
and SHAP-style explanations, forecasting with backtests and error bands, and automatic segment
discovery for variance explanation. Each is already specified as a chart type with prerequisites, so
the work is implementation rather than design.
