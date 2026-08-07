# analyse.pdhc — progress

## Status: SCAFFOLD + PORT COMPLETE (local), NOT deployed. #538 + #539.

Built 2026-08-07. Greenfield containerised service scaffolded from the
cd-assist.pdhc/cd_assist_app template; group/population engine ported from
dashboard.pdhc. Local tests green; server deploy + cutover tickets (#540/#541/
#543) are separate and operator-handled.

## 1.a Scaffold (#538) — DONE
- Containerised shape copied from `cd-assist.pdhc/cd_assist_app`: Dockerfile,
  docker-compose.yml (`analyse_pdhc_app`/`analyse_pdhc_db`, 127.0.0.1:9110/9111,
  volume `analyse_pdhc_pgdata`, project pin `analyse_pdhc`), entrypoint.sh
  (`flask db upgrade` → gunicorn on 0.0.0.0:9110 published loopback-only),
  wsgi.py, requirements.txt, .env.example, .dockerignore/.gitignore,
  migrations/ scaffold, `start.sh` (CLAUDE.md §6/§8 contract, own-ports-only).
- `app/extensions.py` (db/migrate + dialect-aware JSONB), `app/version.py`.
- Health at BOTH `/healthz` and `/api/v1/health` — canonical §10 shape
  (status/database/service/version, HTTP 503 on degraded, CORS to www.pdhc.se).

## 1.b Auth gate (analysis-phase) — DONE
- `app/auth.py` mirrors dashboard.pdhc/app/auth.py but is **analysis-phase
  only** (no care-delivery split): `has_analysis_access(blob)` = SU admin OR
  (professional AND 'analysis' in session_phases, dual-read effective_phases).
- Global `install_request_loader` before_request: service-key → dev-SU (off) →
  SSO bearer re-validation (Rule 11, no blob cache) → `has_analysis_access`.
- Service-key bypass (`KNOWN_SERVICES`): gateway.pdhc + monitor.pdhc, each
  presenting its own key. analyse's OWN outbound identity to CDR2–6 is
  `ANALYSE_PDHC_SERVICE_KEY` (federation fanout: `X-Source-Service: analyse.pdhc`).
- `role_guards.py` (researcher_required/admin_required) ported verbatim.
- SSO web login: **ported dashboard.pdhc/app/routes/auth.py** (`/auth/login|
  callback|logout|logged-out`), already analysis-gated (`has_analysis_access`),
  session token consumed + re-validated by the global loader. `/` researcher
  workspace shell (`routes/views.py` + templates/researcher_workspace.html).
  - DECISION: used dashboard's before_request + routes/auth.py flow (not the
    greenfield cd-assist `api/sso_web.py` decorator flow). Reason: the federated
    service-key endpoints REQUIRE the global before_request to populate
    `g.access_blob.service_source`; a decorator-per-route model would not. This
    is the flow researcher.py was actually written against, so the port is
    faithful. Callback path is `/auth/callback` (set SSO_CALLBACK_URL to match).
- `AUTH_MODE=off` dev-SU blob carries `session_phases:["analysis"]` (Rule 23).

## 1.c Port group/population engine (#539) — DONE
- `app/routes/researcher.py` — cohort engine UI+JSON, ported verbatim except
  `DashboardAudit`→`AnalyseAudit`. Includes `register_export_audit_cli`
  (`flask migrate-export-audit-log`).
- `app/analyse/cohort.py` — cohort predicate builder (verbatim).
- `app/analyse/observations_search.py` (#291), `stats.py`/`canonical.py`/
  `openehr.py` (#292) — federated aux endpoints, service-key gated (verbatim).
- `app/analyse/federation.py` + `aggregations.py` — read core, taken from
  dashboard (source of truth, D5). Only edit: fanout outbound identity
  `DASHBOARD_PDHC_SERVICE_KEY`/`dashboard.pdhc` → `ANALYSE_PDHC_SERVICE_KEY`/
  `analyse.pdhc`.
- `app/services/{audit,ips_client,session_headers}.py` — ported. audit.py →
  `AnalyseAudit` (table `analyse_audit`); ips_client outbound key →
  `ANALYSE_PDHC_SERVICE_KEY`.
- `cdr1_client.py` deliberately NOT ported (care-delivery CDR1 path = cd-assist).

## DB — DONE
- Own Postgres. Tables: `users` (SU bootstrap), `cohort` (ported column-for-
  column from dashboard), `analyse_audit` (ported from `dashboard_audit`,
  renamed). Flask-Migrate wired via `extensions.migrate`.
- Single alembic head: **`0001_initial`** (`flask db heads` → `0001_initial (head)`).
- Migration is Postgres-only (postgresql.JSONB/UUID, server_default NOW()) —
  same proven pattern as dashboard/cd-assist; tests use dialect-aware
  `create_all()` on SQLite.

## Tests — 38 passed (offline)
`cd analyse_app && PYTHONPATH=. .venv/bin/python -m pytest -q` → **38 passed in ~1s**.
- test_health.py (3) — §10 shape at both paths, no-auth.
- test_researcher_flow.py (5) — define/list/histogram-merge/export/scatter,
  `requests.request` patched; consent allow-all (conftest autouse).
- test_analyse_aux.py (12) — stats/canonical/openehr/observations service-key
  gate + merge, `fanout` patched.
- test_auth_gate.py (10) — has_analysis_access unit, service-key 401/403, SSO
  no-bearer redirect, stale-token revalidation redirect, valid-but-no-analysis 403.
- test_sso_web.py (8) — login/callback/logout, gate re-validation, landing shell.
- `py_compile` clean across all modules. venv is Python 3.14.3 (host);
  container is python:3.12-slim.

## Known gaps / assumptions
- NOT deployed to the macmini; NOT git-initialised/committed (operator verifies
  then commits). No other service touched (gateway/CDRs untouched — #540/#541).
- SSO client creds, the three service keys, and CDR2–6 URLs are `.env` blanks
  the operator fills on the server (`.env.example` documents each).
- `top_rules.md` intentionally NOT created (frozen-file rule; none existed).
- The `flask migrate-export-audit-log` CLI is carried over from dashboard for
  parity; there is no analyse-side legacy file log to migrate (harmless no-op).

## newtask.txt = next focus
Deploy (1.c/1.d in readme) + #540 gateway ANALYSE_BASE_URL repoint + #541 CDR
identity flip.
