# Security

Two different questions hide inside "is this safe to ship". This document
answers both, because passing a dependency audit tells you nothing about
whether the application itself is sound, and a clean application tells you
nothing about a CVE in a transitive package.

---

## 1. The pre-ship gate

One command:

```bash
make verify          # lint (incl. bandit rules) + tests + dependency audits
```

Or each gate on its own:

| Gate | Command | Fails when |
| --- | --- | --- |
| Backend lint and security rules | `cd backend && ruff check app tests` | Any lint or bandit (`S`) finding |
| Backend formatting | `ruff format --check app tests` | Formatting drift |
| Backend tests | `pytest` | Any of 148 tests fails |
| Backend dependency audit | `pip-audit -r requirements.txt --strict` | Any known advisory in the pinned runtime set |
| Frontend types | `npm run typecheck` | Any strict TypeScript error |
| Frontend tests | `npm run test` | Any of 49 tests fails |
| Frontend runtime dependency audit | `npm audit --omit=dev --audit-level=low` | **Any** advisory in code that reaches a browser |
| Frontend full-tree audit | `npm audit --audit-level=moderate` | Moderate or above anywhere, including build tooling |
| Frontend build | `npm run build` | Build failure |

The two-stage `npm audit` is deliberate. Runtime dependencies ship to your
users, so the bar there is *any* severity. Build and test tooling (Vite, Vitest,
Playwright) never reaches production, so a low-severity dev advisory should be
visible without blocking a release — but moderate and above still fails, because
a compromised build tool is a supply-chain problem regardless of where it runs.

### Current state

Verified on the current pinned set:

```
pip-audit -r requirements.txt      → No known vulnerabilities found
npm audit --omit=dev               → found 0 vulnerabilities
npm audit (full tree)              → found 0 vulnerabilities
ruff check app tests (with S)      → All checks passed
pytest                             → 148 passed
npm run typecheck / test / build   → clean
```

This came from fixing real findings, not from lowering a threshold. See the
1.3.0 entry in [CHANGELOG.md](CHANGELOG.md) for what moved and why.

### In CI

`.github/workflows/ci.yml` runs all of the above on every push and pull
request, plus three things a laptop will not do:

- **Container scan** — Trivy against the built backend image, failing on
  HIGH/CRITICAL with fixes available, which is how you catch a CVE in the
  Debian base layer rather than in your own dependencies.
- **Secret scan** — gitleaks over full history, because a key that was
  committed and then deleted is still a leaked key.
- **CodeQL** — `security-extended` queries for Python and TypeScript.

It also runs weekly on a schedule. Advisories are published against code that
nobody touched; a gate that only fires on a commit will tell you a dependency
was clean the day you last changed it, which is not the same as clean today.

---

## 2. The application's own threat model

Dependency scanners cannot see any of this, so each item below is enforced in
code and covered by a test.

### Untrusted file upload
- Format is identified from magic bytes, not the extension. Legacy or encrypted
  Office files, PDFs and binary payloads are refused with an actionable message.
- Filenames are sanitised before they touch the filesystem (`../../etc/passwd`
  becomes `passwd`); the stored name is separate from the display name.
- Size is enforced while streaming, not after, so an oversized upload cannot
  fill the disk before the check runs.
- Workbooks are opened read-only with `data_only`; macros are never executed.
- Tests: `test_profiling_pipeline.py` (sanitisation, content sniffing, empty and
  header-only files), `test_api.py::test_unsupported_file_is_rejected_with_a_useful_error`.

### Query execution
- No LLM-authored SQL or Python is ever executed. A question becomes an
  `AnalysisPlan`, which is validated against the real column list and then
  compiled with an allow-list of aggregate functions.
- Identifiers are resolved against the dataset schema and quoted; every user
  value is bound as a parameter. The one `# noqa: S608` in the codebase sits on
  that line with the reasoning written next to it.
- A row cap and a wall-clock timeout bound the work; the timeout interrupts the
  DuckDB connection rather than leaving a query running.
- Tests: `test_plan_rejects_unknown_columns`, `test_filter_values_are_parameterized`,
  `test_row_limit_is_enforced`, `test_preview_filter_value_is_never_executed`.

### Prompt injection
- All dataset content and all user text is wrapped in
  `<untrusted_dataset_content>` markers, and the system prompt classifies
  anything inside them as data that may not be followed as instruction.
- Defence in depth matters more than the wrapper: even a successful injection
  cannot produce a chart of a column that does not exist, or a query that was
  not compiled from an allow-listed plan, because every model response is
  validated against the real schema before it is used.
- Tests: `test_model_output_with_fake_columns_is_rejected`,
  `test_spec_validation_rejects_invented_columns`.

### Secrets
- Provider keys are read from backend environment variables only. No endpoint
  returns them, no log line prints them, and no frontend state holds them.
- The usage panel reports provider, model and token counts — never credentials.
- Tests: `test_health_exposes_providers_without_secrets`,
  `test_usage_response_contains_no_secrets`,
  `test_usage_records_never_carry_prompts_or_keys`.

### Personal data
- Columns matching email, phone, government ID, account, card, credential,
  person-name, address or health patterns are detected and flagged.
- Sensitive values are masked in sample values, in anomaly row previews, in the
  dataset preview and in rule examples. Filtering *on* a personal-data column in
  the preview is refused server-side.
- The model payload carries a name, a role and a sensitivity flag for such a
  column — never values, and never raw rows.
- Tests: `test_compact_profile_excludes_raw_and_sensitive_values`,
  `test_anomaly_preview_hides_sensitive_columns`,
  `test_preview_refuses_to_filter_on_personal_data`.

### Output handling
- Exported CSV values beginning `=`, `+`, `-` or `@` are prefixed so a
  spreadsheet cannot execute them; the quality report omits failing values
  entirely.
- Exported HTML is escaped. No model-supplied HTML, SQL, URL or formula is ever
  rendered unvalidated.
- Tests: `test_formula_injection_is_neutralized`, `test_csv_export_is_safe`.

### Filesystem and cache
- Dataset ids are regex-validated and artifact filenames come from an enum, so
  no request can steer a path. The workspace root is re-checked on every
  resolve.
- Directories are `0o700`, files `0o600`. Writes are atomic (temp file plus
  rename) under an advisory lock.
- Keys, authorization data, complete prompts, unmasked samples and executable
  content are never written to the cache.
- Tests: `test_dataset_id_is_validated_against_path_traversal`,
  `test_artifact_path_cannot_escape_the_workspace`,
  `test_cache_report_carries_no_data_values`.

### Transport and headers
- CORS is an explicit origin allow-list from `CORS_ORIGINS`, not a wildcard, and
  credentials are disabled.
- `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` are set
  on every response, and every response carries a correlation id.
- Errors are typed and carry a request id; tracebacks and file paths are logged
  server-side, never returned.

---

## 3. What this does *not* cover

Be clear about these before exposing the app to anyone but yourself.

- **No authentication, no authorisation, no tenancy.** Anyone who can reach the
  port can read every dataset in the workspace. Do not put it on the internet
  as-is; put it behind your identity provider, or keep it on localhost.
- **No rate limiting.** A single client can occupy the profiler. Add limits at
  the reverse proxy.
- **No encryption at rest.** Uploads and cache artifacts sit on the local
  filesystem in plaintext. Use an encrypted volume if the data warrants it.
- **Sensitive-column detection is a heuristic.** It catches common patterns; a
  column of free-text notes containing a customer's name will not be flagged.
  Review the detected roles on the Data Profile page before enabling a provider.
- **Sending data to a third party is your decision.** With OpenAI or Anthropic
  configured, the compact profile leaves your network. It contains no raw rows,
  but column names and summary statistics can themselves be sensitive. The
  built-in narrator keeps everything local.
- **`ALLOW_MASKED_SAMPLE_TO_LLM`** is off by default. Turning it on sends masked
  example values to the provider. Treat that as a governance decision, not a
  configuration tweak.
- **Playwright end-to-end tests are not part of the gate above** because they
  need a browser. Run `npm run e2e` with both servers up before a release.

---

## 4. Keeping it clean

- **Pin exactly, bump deliberately.** `requirements.txt` and
  `package-lock.json` are pinned; `npm ci` is used in CI so a build cannot drift.
- **After any bump, run `make verify`.** An upgrade that fixes a CVE and breaks
  a chart is not an improvement, which is why the audit and the tests are in the
  same gate.
- **Enable Dependabot** (or Renovate) for weekly dependency PRs; the CI gate is
  what makes those PRs safe to merge quickly.
- **Rotate provider keys** on the schedule your organisation requires. Deleting
  a session removes its data and cache but has no bearing on a key.

## Reporting a vulnerability

This is a starting-point application rather than a hosted service. If you find a
problem in it, record it with your own security process; if you are running it
for others, publish an address here and state a response time you can keep.
