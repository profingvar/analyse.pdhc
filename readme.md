# analyse.pdhc — deployment plan

The **GROUP / population + federated-events** half of the old dashboard,
extracted into its own containerised FHIR-R5 microservice (docs/470_scoping.md).
Hosts the researcher/cohort engine and the gateway/monitor-facing federated
endpoints. Gated on the **ANALYSIS phase** (NOT care-delivery — that half stays
in dashboard, being renamed cd-assist).

Tickets: **#538** (scaffold) + **#539** (port the group/population engine).
Follow-ups (separate tickets, operator-handled): #540 gateway `ANALYSE_BASE_URL`
repoint, #541 CDR2–6 read-identity flip, #542 cutover, #543 delete the group
half from dashboard(cd-assist).

## Identity / ports (CLAUDE.md §3)

| thing | value |
|-------|-------|
| app port | **9110** (127.0.0.1 only) |
| db port  | **9111** (127.0.0.1 only) |
| compose project | `analyse_pdhc` (PINNED in .env) |
| containers | `analyse_pdhc_app` / `analyse_pdhc_db` |
| health | `/healthz` **and** `/api/v1/health` (canonical §10 shape, 503 on degraded) |
| service name string | `analyse.pdhc` |
| own outbound CDR read key | `ANALYSE_PDHC_SERVICE_KEY` |
| inbound service callers | gateway.pdhc (`GATEWAY_PDHC_SERVICE_KEY`), monitor.pdhc (`MONITOR_PDHC_SERVICE_KEY`) |

## Deploy plan

### 1.a — local bring-up (done)
- `cp analyse_app/.env.example analyse_app/.env`, fill `POSTGRES_PASSWORD`,
  `SECRET_KEY`, `ANALYSE_PDHC_SERVICE_KEY`. Leave `AUTH_MODE=off` for local dev.
- `./start.sh` — builds + brings up ONLY the `analyse_pdhc` compose project,
  runs `flask db upgrade` in the container entrypoint, smoke-probes `/healthz`.

### 1.b — tests (offline, done)
- `python3 -m venv analyse_app/.venv && analyse_app/.venv/bin/pip install -r
  analyse_app/requirements.txt pytest`
- `cd analyse_app && PYTHONPATH=. .venv/bin/python -m pytest -q` → 38 passed.

### 1.c — server deploy (NOT done here — operator)
- Release-symlink layout under `/usr/local/www/analyse.pdhc/` (CLAUDE.md §7).
- Fill `.env` with `AUTH_MODE=sso`, real SSO client creds (registered as the
  `analyse` consumer in sso.pdhc), `SSO_CALLBACK_URL=https://analyse.pdhc.se/auth/callback`,
  the CDR2–6 `CDR_ENDPOINTS` loopback URLs, `IPS_BASE_URL`, and the three
  service keys.
- `flask create-su --username <op> --password <pw>` (Rule 23 bootstrap).
- Reverse-proxy vhost `analyse.pdhc.se` → 127.0.0.1:9110 (operator).

### 1.d — cutover dependencies (separate tickets)
- **#540** gateway.pdhc `ANALYSE_BASE_URL` → analyse.pdhc (hard dependency for
  the #291 observations pull).
- **#541** flip CDR2–6 to accept `X-Source-Service: analyse.pdhc` +
  `ANALYSE_PDHC_SERVICE_KEY` for analyse reads.
- **#543** delete the group half (researcher + federated endpoints) from
  dashboard(cd-assist) once analyse is live.

## What is IN this service
- `POST/GET /api/cohort`, `/api/cohort/<id>/{histogram,boxplot,scatter,trend,export}`
  — researcher cohort engine (analysis-phase, `researcher_required`, audited).
- `/api/v1/observations` (#291, gateway/monitor service-key).
- `/api/v1/stats`, `/api/v1/canonical/<table>`, `/api/v1/openehr/*` (#292,
  service-key).
- SSO web login (`/auth/login|callback|logout`) + `/` researcher workspace shell.

## What is NOT here (belongs to cd-assist)
Nurse single-patient views, `/charts`, CDR1 care-delivery reads, patient picker,
saved designs, spärr per-patient lift. Do not add them here.
