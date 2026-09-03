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

## LIVE CUTOVER — 2026-08-08 (analyse.pdhc deployed + wired)
- SSO: client analyse-pdhc registered in sso .env; callback+origin added to
  ALLOWED_CALLBACK_URLS/ALLOWED_ORIGINS. analyse SSO login works.
- vhost: analyse.pdhc.se (letsencrypt, /healthz 200), nginx sites-available/enabled.
- deploy: /usr/local/www/analyse.pdhc/current, docker-compose analyse_pdhc,
  app 9110 / db 9111, alembic head 0001_initial, /healthz 200 local+public.
- #541: cdr2-5 trust analyse.pdhc (surgical in-place patch — prod behind local
  git, see memory infra_cdr_prod_behind_local_git). analyse federates cdr2-5
  (registry=4). cdr6 (sim-only codebase) deferred + dropped from CDR_ENDPOINTS.
- #540: gateway ANALYSE_BASE_URL=http://host.docker.internal:9110; gateway->analyse
  reachable (200), gateway health 200, cdr1 forwarder intact.
- REMAINING: #543 remove dashboard group-half + dashboard CDR read-identity;
  #547 rebrand dashboard->cd-assist + www cards; deploy #546 nurse fold; #544
  cleanup + commit. dashboard prod likely behind local git too — patch carefully.

## 2026-09-03 — #579 Stage 2 (per-patient dashboard + full spärr + admin log)
Continuation of #578. Built locally, NOT deployed (analyse cutover is
operator-blocked). 54 tests pass.

Delivered:
- **Item 1 — per-patient Dashboard view.** `GET /api/patient/<guid>?cdr_ids=`
  (`app/analyse/patient_detail.py`) fans out one patient-filtered
  `/api/v1/fhir/Observation` per selected CDR, groups into per-concept series
  (code from `code.coding[0]`, value from `valueQuantity`, unit, points sorted
  by date, latest). Page shell `/patient/<guid>` → `patient_detail.html`
  (demographics, CDR picker, per-series inline-SVG sparklines). The list rows
  already linked here.
- **Item 2 — full spärr enforcement + logging.** Non-admin + active block →
  data HIDDEN (empty series, `fanout_mode:"hidden"`, banner), the CDR read is
  short-circuited (no fetch). Admin + active block → break-glass EXPOSURE:
  data returned and an `AnalyseAudit` row is written with
  `event_type=sparr_lift_exposure` + `payload_snapshot.block_guids`. Non-admin
  fail-closed when ips is unreachable. Admin oversight view
  `/admin/sparr-log` (+ `GET /api/admin/sparr-log`, admin_required) surfaces
  sparr_lift_exposure/sparr_hidden/admin_override rows. audit_read is the OUTER
  decorator on the patient route so 403 denials are logged too.
- **Item 4 — admin-list scope DECISION.** Admin list stays data-scoped
  (patients with CDR data in the selected CDRs), NOT "all ips patients". Reason:
  the "choose patient" list is a working entry point; a zero-data patient has
  nothing to open, and an unbounded cross-org roster is a privacy-broad default.
  A specific no-data patient is reachable by direct guid, not by browse.

Remaining in #579 (operator-blocked, cannot close):
- **Item 3 — live-shape verification.** patient_detail/patient_list parse
  tolerantly, but the exact ips `/clinics/<g>/patients` and CDR
  `/fhir/Observation` JSON must be confirmed against the running services once
  analyse is deployed (esp. org_guid location on the FHIR Observation for a
  finer-grained per-clinic spärr filter than today's coarse patient-level hide).
- **Item 5 — deploy/cutover.** SSO client reg, service keys, CDR2–6 URLs,
  vhost analyse.pdhc.se + DNS/TLS. HIGH blast — operator-coordinated.

Note (SQLite test artifact): the `analyse_audit.patient_guid` UUID column takes
NUMERIC affinity under SQLite, so an all-digit guid is coerced to a float on
write. Prod Postgres has a real `uuid` column and is unaffected; tests use
uuid4-shaped guids (hex letters) to keep TEXT affinity.

## 2026-09-03 — #579 item-3 DONE (live-shape verification against cdr2–5 + ips)
Verified against the running platform (analyse deployed but on the old
0.1.0-scaffold image; reform code still local). Probed cdr2 (9146) as
analyse.pdhc service key, and read ips.pdhc source for exact response shapes.

VERIFIED SHAPES:
- CDR Observation: `code.coding[0]` = LOINC/termbank standard code,
  `coding[1]` = plan.pdhc Concept guid (system `.../Concept`, always present).
  Owning clinic = `meta.security[code=="org_guid"].display`. Value in
  `valueQuantity.value`+`unit`(+`code`). `subject.reference` = `Patient/<guid>`.
  `?patient=<guid>` filter works.
- ips `/api/v1/clinics/<g>/patients` = FLAT ARRAY of PatientIndex.to_dict():
  `{guid, family_name, given_name, birth_date, is_active, ...}` (NOT `name`).
- ips auth: REJECTS analyse's service key (401). ips calls must carry the
  user's SSO token — which lives in `session["sso_token"]`, not a header.

FOUR BUGS the verification surfaced, all fixed:
  A. clinical._bearer() read the Authorization header → None for browser
     (cookie-auth) calls → every ips call 401 → non-admin list would show ALL
     patients spärrade + per-patient always failed. Now reads session token.
  B. patient_directory._name_of only handled `name`/FHIR HumanName → clinic
     patient names were blank. Added the flat family_name/given_name branch.
  C. patient_detail grouped series on coding[0] (LOINC, absent for unmapped
     concepts). Now keys on the plan.pdhc Concept guid, labels by LOINC.
  D. Coarse patient-level hide replaced by PER-CLINIC spärr: drop observations
     whose meta.security org_guid ∈ blocked clinics (non-admin); admin
     break-glass exposes them and logs `sparr_lift_exposure` with exposed_orgs.
     Block fetch fails OPEN with banner (platform legal model); a hard ips
     outage (ips_unavailable) fails closed for a non-admin.

59 tests pass. Residual (needs a real user login, deferred to cutover smoke):
capture a live `/clinics/<g>/patients` body with a user token to reconfirm B
end-to-end (source-confirmed, not yet round-tripped live).

Item 5 (deploy/cutover) remains the only open #579 item — operator-blocked.

### #579 item-3 — one OPEN spärr question (must verify at cutover)
The per-clinic filter needs un-redacted `source_scope_id` from ips `/blocks`.
Per memory infra_ips_auth_header_scheme, ips REDACTS `source_scope_id → None`
for callers it deems unrelated to the patient (and 403s service accounts). If
analyse's analysis-phase professional token is treated as "unrelated", the
block list comes back with `source_scope_id=None` → blocked_clinic_ids is empty
→ the filter silently FAILS OPEN (spärrad data shown). Resolve at cutover by
either: (i) confirming the professional token is "related" enough for the
un-redacted list; (ii) registering an analyse service ApiKey in ips and
fetching the list server-to-server; or (iii) switching the checker to per-org
`GET /blocks/check?source_clinic_id=<org>` (relationship-free, un-redacted).
Code currently does (path i) — safe only if the assumption holds.

### #579 item-3 — spärr endpoint FIXED (fail-open risk closed)
Read ips.pdhc blocks_routes.py: the `/blocks` LIST is @require_auth +
_can_act_on_patient (403s an unrelated caller) AND redacts source_scope_id→None
except the caller's own clinics — so the earlier per-clinic filter that fetched
the list would fail OPEN for a cross-clinic analysis caller. Switched to the two
RELATIONSHIP-FREE, un-redacted predicates:
  - `GET /blocks/check?source_clinic_id=<org>` → {is_blocked, blocking_scopes}
    — per-patient view checks each observation's meta.security org_guid; memoised
    one call per distinct producing-org. None (ips error) → fail SAFE (hide for
    a care caller; admin still sees, un-logged).
  - `GET /blocks/metadata` → {blocked_source_count} — the list's coarse badge.
IpsClient.check_source_blocked / patient_has_block added + unit-tested
(test_ips_blocks.py). patient_detail now takes a block_check(org)->bool|None
callable instead of a pre-fetched block list. 62 tests pass.

Residual for cutover smoke (needs a real analysis-phase login): round-trip a
real blocked patient end-to-end (confirm /blocks/check accepts the professional
token as expected) + confirm a live /clinics/<g>/patients body (names). Both are
source-confirmed; only the live token round-trip is unverified.
