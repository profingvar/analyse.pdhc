# analyse.pdhc — technical documentation

## Purpose

analyse.pdhc is the **GROUP / population + federated-events** half of the old
dashboard, extracted into its own FHIR-R5 microservice (docs/470_scoping.md).
It hosts:

- the **researcher / cohort engine** (define a cohort by a JSON predicate,
  resolve members across the CDR federation, then histogram / boxplot /
  scatter / trend / CSV-export aggregations), and
- the **gateway/monitor-facing federated endpoints** (`/api/v1/observations`
  #291; `/api/v1/stats`, `/api/v1/canonical/<table>`, `/api/v1/openehr/*` #292).
  The two that return a named patient's rows — `canonical` and `openehr` —
  require a declared `X-Access-Purpose` since #700 and forward it to every
  CDR's consent gate; see `app/analyse/purpose.py` for why analyse refuses
  rather than declaring a purpose on the caller's behalf.

It is gated on the **ANALYSIS phase**. The individual / point-of-care half
(nurse, `/charts`, patient single-view, CDR1 care-delivery reads) lives in
cd-assist (host `dashboard.pdhc.se`, unchanged) and is deliberately absent here.

## Architecture

```
browser ──SSO login──▶ /auth/login → sso.pdhc → /auth/callback (session token)
                        │
gateway.pdhc ──svc-key──┤ before_request loader (app/auth.py)
                        │   • service-key   → service blob (service_source)
                        │   • AUTH_MODE=off  → dev-SU blob
                        │   • AUTH_MODE=sso  → re-validate bearer every request
                        ▼   • gate: has_analysis_access (SU OR prof+analysis)
        ┌───────────────────────────────────────────────┐
        │ researcher_api  (/api/cohort…)  researcher_required + @audit_read
        │ observations_search (/api/v1/observations)  service_source gated
        │ analyse_stats / canonical / openehr (/api/v1/…) service_source gated
        └───────────────────────────────────────────────┘
                        │  app/analyse/federation.fanout (ThreadPool)
                        ▼  X-Source-Service: analyse.pdhc + ANALYSE_PDHC_SERVICE_KEY
                    CDR2 … CDR6   (/api/v1/fhir/Observation, /api/v1/stats, …)
```

### Auth (app/auth.py)
- `has_analysis_access(blob)` = `is_su_admin` OR (`user_type == "professional"`
  AND `"analysis" in session_phases`, dual-read `effective_phases`).
- `install_request_loader` — one `before_request`. Order: public paths
  (`/auth/*`, `/healthz`, `/api/v1/health`, `/static/*`) → service-key
  (`KNOWN_SERVICES`) → `AUTH_MODE=off` dev-SU → SSO bearer **re-validated on
  every request** against sso.pdhc `/api/auth/me/service` (Rule 11, no blob
  cache) → `has_analysis_access` gate.
- `KNOWN_SERVICES = {gateway.pdhc: GATEWAY_PDHC_SERVICE_KEY}` — each sibling
  presents its own key. This dict decides **who authenticates**, not what they
  may read; see `app/analyse/callers.py` for the latter. `monitor.pdhc` was the
  second entry until #727 removed it.
  Service callers get a machine blob with `service_source` set and NO clinical
  roles / admin bit, so they can reach ONLY the federated `/api/v1/*` endpoints
  (which self-gate on `service_source`); the researcher UI (`researcher_required`)
  needs a real operator session.
- `ANALYSE_PDHC_SERVICE_KEY` is analyse's OWN outbound identity to CDR2–6 (set
  in the federation fanout), distinct from the inbound keys above.

### Caller allowlists (app/analyse/callers.py) — #727
`KNOWN_SERVICES` decides **who authenticates**. This module decides **which
endpoints each authenticated caller may reach**, split by what the endpoint
returns rather than by how much the caller is trusted:

| Set | Endpoints | Members |
|---|---|---|
| `PATIENT_DATA_CALLERS` | `/api/v1/canonical/<table>`, `/api/v1/openehr/*`, `/api/v1/observations` — all fan a **named patient's** rows out of every CDR | `gateway.pdhc` |
| `AGGREGATE_CALLERS` | `/api/v1/stats` — per-CDR row counts, nothing patient-identifying | `gateway.pdhc` |

Both hold the same one member today, and they are still two sets: the next
monitoring or CI identity belongs in `AGGREGATE_CALLERS` alone, and having
somewhere correct to put it is what stops it being pasted into all four
endpoints the way the last one was. `AGGREGATE_CALLERS` is defined as a
superset of `PATIENT_DATA_CALLERS`, so the counts-only endpoint can never
refuse a caller the patient-data ones admit.

`caller_check(blob, allowed)` returns `None` to proceed or `(payload, status)`
— 401 when there is no `service_source` at all, 403 when it is not on the list.
The 403 body is identical either way, so a rejected caller learns that it is
not allowed here and nothing about who is.

Before #727 this was one literal, `{"gateway.pdhc", "monitor.pdhc"}`, hand-copied
into all four route modules with no test of any kind. `monitor.pdhc` was **not a
service**: a synthetic service-key identity created 2026-04-28 so the Playwright,
perf and chaos suites could bypass SSO (`plans/*_2026-04-28.md`), with no repo,
container or port. It had patient-data access because the list was copied, not
because anything asked for it. #727 removed it from `KNOWN_SERVICES` too, so it
no longer authenticates; `MONITOR_PDHC_SERVICE_KEY` is read nowhere and is inert
wherever it is still set. Unsetting it on the hosts and rotating the key remain
operator actions.

This is orthogonal to `app/analyse/purpose.py` (#700): that constrains **what**
a caller may declare it is reading for. A caller must pass both.

### Web login (app/routes/auth.py, ported from dashboard.pdhc)
`/auth/login` → sso.pdhc (anti-CSRF `state`), `/auth/callback` validates the
returned token, admits only `has_analysis_access`, stores `session['sso_token']`
(re-validated each request by the loader), `/auth/logout` clears + revokes.
`/` renders the researcher workspace shell (`routes/views.py`).

### Read core (app/analyse/federation.py + aggregations.py)
Copied from dashboard.pdhc (source of truth per decision D5 — mirror any fix
across dashboard/cd-assist/analyse). `CdrRegistry.from_config` reads
`CDR_ENDPOINTS`; `fanout` calls every CDR concurrently with per-CDR timeout and
partial-result tolerance (mode = complete|degraded|error). Aggregators:
`merge_histograms`, `merge_agp_bands`, `concat_series`, `lttb_downsample`;
`aggregations.compute_stats/compute_agp` produce the cdr1-shaped Parameters the
mergers consume.

### Consent (app/services/ips_client.py)
Cohort member sets pass through ips.pdhc's `analysis-filter` (purpose=research)
before persist/aggregate — ips owns the consent flags (D1 #404); the verdict is
never computed locally and **fails closed** (503) if ips is unreachable.
analyse calls it with `ANALYSE_PDHC_SERVICE_KEY`.

## Data model (app/models)
- `users` — local SSO-caller mirror; `flask create-su` bootstrap (Rule 23).
- `cohort` — persisted cohort definitions (guid, filter JSONB, members JSONB,
  n, owner_label, created_at). Ported column-for-column from dashboard.
- `analyse_audit` — read-side PDL Ch 4 §3 kontroller log, one row per
  patient/aggregate-touching read incl. 4xx denials (`@audit_read`). Ported
  from dashboard's `dashboard_audit`; X1 tuple (person/role/purpose/access_basis)
  in `payload_snapshot`, purpose route-classed (research for `/api/cohort`).

`JSONB` is dialect-aware (real JSONB on Postgres, plain JSON on SQLite for the
hermetic test suite). Single alembic head `0001_initial`.

## Config (analyse_app/.env.example)
`APP_PORT=9110`, `DB_PORT=9111` (127.0.0.1 only), `COMPOSE_PROJECT_NAME=analyse_pdhc`,
`AUTH_MODE`, `SSO_*`, `GATEWAY_PDHC_SERVICE_KEY`, `MONITOR_PDHC_SERVICE_KEY`,
`ANALYSE_PDHC_SERVICE_KEY`, `IPS_BASE_URL`, `CDR_ENDPOINTS` (comma-sep;
`https://cdrN.pdhc.se` or `cdrN=http://127.0.0.1:PORT`), `CDR_FANOUT_TIMEOUT`.

## Run / test
- Local container: `cp analyse_app/.env.example analyse_app/.env` (fill secrets),
  `./start.sh`. Loopback-only; migrations run in the container entrypoint.
- Tests (offline): `cd analyse_app && PYTHONPATH=. .venv/bin/python -m pytest -q`
  → 38 passed. SQLite in-memory StaticPool, AUTH_MODE=off, CDR fanout + SSO
  revalidation + ips consent all patched.

## Deploy / cutover
Server layout CLAUDE.md §7. Hard follow-ups: **#540** gateway
`ANALYSE_BASE_URL` repoint, **#541** CDR2–6 read-identity flip, **#543** delete
the group half from dashboard(cd-assist). See readme.md.

## Port Allocation

All ports bind to `127.0.0.1` (loopback only); external traffic arrives
via the reverse proxy.

| Port | Service |
|------|---------|
| 9110 | Flask application (Gunicorn) |
| 9111 | PostgreSQL database |
