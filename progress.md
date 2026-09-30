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

## 2026-09-03 — #579 item 5 DEPLOYED (reform live on analyse.pdhc)
Discovery: analyse was ALREADY deployed (container analyse_pdhc_app :9110,
vhost analyse.pdhc.se live since 2026-08-08) running the OLD 0.1.0-scaffold
image — so item-5's "operator-blocked" infra (SSO client, service keys, CDR
URLs, vhost, DNS/TLS) was already satisfied. Deploying the reform was therefore
a routine redeploy of our own service (within envelope), not the high-blast
cutover the ticket assumed.

Deploy (2026-09-03T06-42-49Z release):
- Verified deployed snapshot == commit 0f47896 byte-for-byte (no server-only
  source edits; only .env is server-state).
- Safety: tar of old release + pg_dump of analyse_pdhc_db →
  ~/backups/predeploy/analyse.pdhc/ (2026-09-03T06:42Z).
- rsync HEAD analyse_app/ → new release; preserved .env (mode 600);
  COMPOSE_PROJECT_NAME=analyse_pdhc pinned in .env → volume analyse_pdhc_pgdata
  reused (no data loss); flip current symlink; `docker-compose up -d --build`.
- Verified: /healthz local+public = 200 database:connected version 0.2.0-reform
  (marker bumped to prove new image); flask db upgrade clean (no new migration);
  gunicorn 2 workers no errors; / → 302 /auth/login; all 8 reform routes exist
  (302, not 404). Old release 2026-08-08 retained as rollback.

REMAINING = user live-smoke (SSO-gated, needs the operator to log in at
analyse.pdhc.se): confirm choose-patient list shows org patients + names +
datapoints; a patient dashboard renders series/sparklines; spärr hides/badges
if any blocked patient exists; admin sees /admin/sparr-log. That smoke also
resolves the last item-3 residual (ips token round-trip + /clinics/patients
names live). After it passes, #579 can be closed.

---

## 2026-09-23 — #644 (AN-1): the analysis spec

75 tests pass (43 after the #663 removal, +32 here). Phase 1's first ticket.

**What it is.** `app/spec/` — the declarative, versioned document that is the
only thing a node will execute. The UI and the CLI both produce specs; neither
sends a free-form query to a node.

**Two deliberate departures from the brief**, both from AN-0 discovery:

1. **`purpose` uses the PLATFORM's closed enum**, not the brief's vocabulary.
   The brief's example says `quality_followup`, which is not a real value
   anywhere in PDHC. ips.pdhc owns the enum and cdr enforces it (#664);
   inventing a parallel vocabulary would mean translating at the boundary and
   getting it wrong once. (Gap G6.)
2. **Only SECONDARY-use purposes are accepted** — research, statistics,
   quality_registry. Analysis reads for secondary use by definition, and
   `administration` is *never blocked* by ips, so accepting it would make
   `purpose` a way around consent rather than a way of declaring it. This
   mirrors the guard shipped in cdr #664.

**The reproducibility contract.** Every result carries the spec hash, the
coordinator and node versions, and per-source snapshot times. For that to mean
anything the hash must be a property of the spec's MEANING, not of how it was
written:
- sorted keys, no incidental whitespace, UTF-8
- defaults are INCLUDED, so relying on a default and stating it hash the same
  — otherwise changing a default later would silently re-hash every stored
  spec and old results could no longer be traced
- nulls are NOT dropped, so omitted and explicitly-null agree
- hash is prefixed `sha256:` so it stays verifiable if the algorithm changes
- **snapshots are per source, not global** — federated sources are read at
  different moments and one timestamp would be a fiction

There is a test that hashes the same spec in three subprocesses under
different `PYTHONHASHSEED` values, because a hash that depended on dict
iteration order would be reproducible only within a single run.

**Validation worth knowing about.** Unknown keys are an error, not ignored —
a silently dropped field is a figure computed from something other than what
the analyst wrote. Cross-references resolve at parse time (an analysis or a
group naming an undeclared variable fails here rather than at the node, after
the read). `window_days` and `over_time` require an `index_event`, since day
offsets have no zero point without one. A series variable must say how it
collapses; a `demographics.*` / `meta.*` variable must not. Overlapping groups
are opt-in.

`meta.author_org` — the field cdr #665 added — is a usable variable source,
and takes no agg.

**Schema is GENERATED**, never hand-written, and committed as
`docs/analyse/analysis-spec-v1.schema.json`. `flask spec-schema` regenerates
it. A hand-maintained schema drifts from the models it claims to describe, and
the drift is invisible until a spec validates here and fails at the node.

**New dependency:** pydantic>=2.7. Verified to have a Python 3.14 wheel, the
check ADR-0004 asks for (2.13.5 installs clean).

---

## 2026-09-23 — #645 (AN-2): privacy layer

132 tests pass (+57). The boundary that decides what can physically leave a
node.

**Projection is CONSTRUCTIVE, not a filter.** The output record is built field
by field from the allowlist; the input is never copied and then cleaned. A
blocklist fails open — the day the CDR grows a column nobody anticipated, a
filter passes it through and a projection does not. There is a test for
exactly that: a record with a `newly_added_column` full of PII, asserted
absent. This is the whole reason the brief says allowlist and never blocklist.

The allowlist is **derived from the spec**, not configured: a field is
projectable because a declared variable reads it, or because the engine
structurally needs it. `NEVER_PROJECTABLE` exists so that an attempt to allow
one fails loudly here rather than succeeding quietly downstream — including
`patient_guid` itself, which is pseudonymised before anything else runs.

**Pseudonyms are per project.** `HMAC-SHA256(project_key, patient_guid)`
truncated to 16 hex chars (64 bits; the birthday bound puts a collision around
2^32 patients, far past any cohort here). HMAC rather than a plain hash
because a GUID is drawn from a small enumerable space and an unkeyed digest of
one is reversible by brute force in seconds. Per-project keys are what stop a
pseudonym being a lifelong identifier joinable across every analysis anyone
ever ran.

`ProjectKey` refuses to render itself — `__repr__` and `__str__` return
`<ProjectKey … redacted>`, and `__hash__` uses the project id only. Keys reach
logs by the dullest possible route: someone formats a config object, or an
exception carries locals. This is a floor under that, not a substitute for
keeping keys out of logs. Keys load from the secret store via env, never from
the spec — the spec is signed and travels between services, so anything in it
is visible to every node it reaches.

**There is no inverse function and none should be added.**

**Coarsening is on by default** and every function narrows; a caller must pass
an explicit granularity to widen, and none can be turned off. Dates become day
offsets from the index event — a calendar date plus a diagnosis often
identifies someone, while "day 14 after injury" is what the analysis actually
needed. Calendar time has month and year granularity and deliberately no DAY.
Age becomes a band with an **open-ended 90+** top, because the bands thin out
fast up there and an exact 97 in one region is close to an identifier on its
own. `birth_date()` exists purely to raise — so that reaching for it fails
loudly instead of someone calling `calendar(dob, year)` and getting a
plausible-looking answer.

**Discovery simplified this ticket.** The brief's "if a CDR keys patients by
personnummer, map to the GUID locally" branch was not built: AN-0 established
the platform is GUID-keyed throughout and holds no personnummer in the CDR
observation tables (gap G9).

Test fixtures use obvious sentinels (`FORBIDDEN-PNR`, not a realistic
personnummer) so that if one ever leaks, AN-7's scanner sees something
unmistakable rather than something that merely looks like test data.

---

## 2026-09-23 — #646 (AN-3): disclosure control

196 tests pass (+64). Runs twice by design: at the node before anything
leaves, and at the coordinator after merging — a merge of two individually
safe partials can be unsafe, and a node cannot see what the other nodes sent.

**Secondary suppression is the ticket.** Blanking cells below `k_min` is the
obvious half and the useless half: if a table publishes margins, one
suppressed cell in a row is `total - sum(the rest)`, so suppressing it merely
tells the reader where to look. Every row and column that has any suppression
must carry at least two.

**The bug I wrote first, and the fix.** The naive complement rule — suppress
the smallest remaining cell — cascades. Suppressing a complement in a row can
leave its column with exactly one, which the next sweep fixes, which leaves
another line with one, until the whole table is blank. On the worked example
it suppressed 6 cells of 6.

The fix is to rank candidates by whether their CROSS line already holds a
suppressed cell: such a complement satisfies the row and the column at once
and terminates the cascade. Smallest-count is only the tie-break. Same
example now suppresses 4 of 6 and keeps two real values. Over-suppression is a
genuine cost, not a safe default — a table that blanks itself tells the
analyst nothing and pushes them toward coarser questions.

**Tested as an attack, not as output shape.** `_recover()` in the test file
implements the subtraction attack and iterates to exhaustion, since solving
one cell can expose another. It is run against 40 randomly generated tables;
a single recovery is a real disclosure. There is also a test that the attack
SUCCEEDS against naive primary-only suppression — otherwise the property test
would be proving nothing.

**Other decisions:**
- `k_min` may be raised by a node, never lowered below its floor. A
  coordinator must not be able to talk a node into disclosing more than the
  organisation owning the data allows.
- Merging policies takes the STRICTER of each, never an average and never the
  coordinator's own: the merged result must satisfy every contributing node.
- A true zero is not suppressed. It discloses nothing about an individual and
  "nobody in this group" is often the finding.
- Small histogram bins MERGE rather than drop — a dropped bin changes the
  shape of the distribution silently; a merged one keeps every patient at
  coarser resolution. Trailing bins merge backwards so no patient is lost at
  either end.
- True min and max are never published: each is one identifiable patient at
  the edge of the distribution. A p5–p95 range replaces them.
- A group comparison is released only when EVERY group passes — releasing the
  ones that pass would disclose the one that did not, by difference.
- The differencing guard records REFUSED probes too, or an attacker could
  retry variations indefinitely, each compared only against those that
  happened to be allowed. An identical rerun is allowed: difference zero
  discloses nobody, and re-running a saved recipe must not look like an
  attack. AN-18 hardens the rest.

---

## 2026-09-23 — #647 (AN-4): engine part 1

226 tests pass (+30). describe, histogram, frequency, correlation, each as
`local` / `merge` / `finalize`.

**A real privacy bug, found by my own test.** The first version shipped
t-digest centroids of weight 1 in the partial — and a centroid of weight 1 is
one patient's exact value crossing the wire. t-digest deliberately keeps tail
centroids small, because that is what makes tail quantiles accurate; the
consequence is that the most extreme patients, the ones disclosure control
works hardest to protect, were precisely the ones sitting alone in a centroid.

`TDigest.protect(k_min)` now merges centroids until none describes fewer than
`k_min` patients, and `describe.local` applies it **before** the partial
leaves the node. The cost is tail resolution, which is aligned with the rest
of the design rather than a loss — AN-3 already refuses to publish true minima
and maxima and reports a p5–p95 range instead. There is a regression test
asserting no centroid falls below the floor, and one asserting three
distinctive values never appear in a serialised partial.

**Exactness is a property of the federation, not of the maths.** Mean, SD, CI,
cell counts, chi-square and Pearson are exact when merged, because they are
functions of sums. Median, IQR and cross-node Spearman are not, and say so on
the result rather than in a comment — a reader cannot tell by looking. Single
node Spearman is exact; two-node Spearman is a weighted average of per-node
coefficients and is labelled as such.

Verified against SciPy to 1e-9: mean, SD, the t-based CI, chi-square, and
Pearson across 1, 2 and 4 nodes.

**Other decisions:**
- Histogram bin edges are fixed by the COORDINATOR. If nodes chose their own
  from their own data the counts would not be addable and the merged
  histogram would be a picture of nothing. Mismatched edges raise rather than
  silently produce a plausible chart.
- `propose_edges` uses inner quantiles, not min/max: a range stretched to an
  outlier gives a histogram of empty bins and one spike, and the extremes are
  single patients.
- Correlation is pairwise-complete, so a patient missing one variable still
  contributes to the pairs they do have.
- Missing is carried explicitly at every stage. A mean over 40 of 200 patients
  and a mean over 200 of 200 are different claims.
- Result notes are plain language and never say "effect" or "causes"; there
  are tests asserting the absence of both words.

**ADR-0008: no Polars, no DuckDB.** All three candidates have Python 3.14
wheels, so this is not an availability decision. A node computes sufficient
statistics — one linear pass, no joins, no columnar scans — and the read
dominates it. SciPy is used only for distribution functions, not computation
over data. The ADR records what would change the decision, with numbers
required.

---

## 2026-09-23 — #648 (AN-5): the node

248 tests pass (+22). Unblocked by cdr #664 and #665, both landed today.

**The order of operations is the security argument**, so `runner.py` writes it
out rather than implying it: policy → data_mode → spärr → read → project →
coarsen → compute → suppress → audit. Every step before `compute` can only
reduce what is computed over.

The one that matters: **spärr runs BEFORE the read, not as a filter after**.
An aggregate computed over a blocked patient has already used their data, even
if the number is discarded afterwards. There is a test asserting blocked
patients never appear in what the node asks the CDR for.

**The node declares its purpose** (`X-Access-Purpose`, plus
`X-Research-Project-Guids` for research) — the header cdr #664 added this
afternoon. Without it the CDR passes machine callers through unfiltered, which
for an analysis node means reading data a patient objected to.

**Everything fails closed.** A 503 from the CDR (its own consent filter down)
propagates. Spärr unreachable means *every* patient is treated as blocked, not
none — and that catch is deliberately broad, so a caller handling
`ConsentUnavailable` does not also need a bare `except` to stay safe.

**Policy is owned by the organisation behind the CDR and the coordinator
cannot override it.** A coordinator asking for a lower `k_min` silently gets
the node's; asking for stricter gets what it asked. A policy with no stated
purposes answers *nothing* — the correct posture for a file someone forgot to
fill in. An unknown key fails the load rather than being ignored, because a
typo is a control the organisation believes it has and does not.

`may_use_other_orgs_rows` is implementable at all only because cdr #665 added
`author_org_guid` this afternoon. It defaults to **no**.

**`data_mode` defaults to synthetic** and live requires an explicit
`allow_live`. It must not be possible to point this at real patients by
forgetting a flag.

---

## 2026-09-23 — #650 (AN-7): test harness and the identifier gate

295 tests pass (+47). Built before AN-6 deliberately: a scanner written late
is written against code that already leaks.

**Federated equals pooled, proven.** Data split at random across 1–5 nodes,
three seeds each, for describe (mean, SD vs SciPy's `tstd`), Pearson (vs
`pearsonr`), histogram counts and frequency counts — all to a relative
tolerance of 1e-9.

**Approximate is not the same as unbounded.** The sketch's median is asserted
within 2% of the true median over six seeds, so a regression that quietly
degrades it is caught rather than excused by the `approximate` flag.

**The gate is a gate.** `scripts/gate.sh` runs the suite, scans everything it
printed, and **exits 1** on a hit. Verified both directions: a deliberately
planted guid produces exit 1, and removing it produces exit 0. A gate that
prints FAIL and exits 0 is a report.

It scans OUTPUT, not source — test files legitimately contain concept guids
and `FORBIDDEN-*` sentinels; what must never appear is one of them in
something the code produced.

**Scanner design.** Deliberately crude and noisy: one tuned to avoid false
positives is one that misses the real leak, and a false positive costs a
minute where a missed identifier costs a patient. Personnummer are matched on
**shape, not checksum** — a near-miss in an output is still someone trying to
put one there. Dictionary **keys** are scanned as well as values, because a
dict keyed by patient guid discloses exactly as much as one that stores it.
Pseudonyms (16 hex, no dashes) deliberately do not match, or the gate would
block every real output.

One test asserts the scanner fires on a **real engine result** carrying
guid-shaped values, not only on crafted strings — otherwise it would prove
nothing about the pipeline.

---

## 2026-09-23 — #651 (AN-8) coordinator, #649 (AN-6) CLI

320 tests pass. Built in this order because the CLI's `run` goes through the
coordinator; building the CLI first would have meant writing a throwaway path.

**Signing is over the CANONICAL form** from AN-1, so it commits to the spec's
*meaning* rather than to one serialisation — a node that re-serialises before
verifying still gets the same bytes. Tested with a key-reordered spec.
Constant-time comparison, so a node does not leak how close a forgery was.
HMAC rather than public-key deliberately: coordinator and nodes are one
deployment with a shared secret store and mTLS already establishes who is
talking. A signature answers *is this the spec the coordinator approved*, not
*who sent it*.

**Three merge rules, each tested:**
- An offline node **degrades that source**, it does not fail the run. Losing
  Uppsala must not lose the Östergötland figures. Every source appears in the
  result with its status, so a reader never sees a pooled number without
  knowing what is in it.
- A node whose policy forbids pooling is **excluded from the pooled figure**
  and reported alone. Its organisation permitted a figure attributable to
  them, not a contribution to someone else's total. Tested with an outlying
  no-pool source that must not drag the pooled mean.
- Disclosure runs **again** after merging, under the **strictest**
  contributing policy — a merge of individually safe partials can be unsafe,
  and no single node could have seen that.

**The CLI refuses to print a result the scanner flags** (exit 3) rather than
emitting something the gate would have caught later, in a file somebody had
already sent on.

`--dry-run` reads nothing at all and says so, returning suppressed counts per
source. It exists so an analyst can size a cohort without running an analysis
over it.

---

## 2026-09-23 — #652 (AN-9): engine part 2

340 tests pass (+20). The registry now covers all seven analysis kinds.

**compare_groups** — the node returns the describe partial per group, so
Welch's t, the mean difference with CI and the standardised mean difference
are all exact across sources. Verified against `scipy.ttest_ind(equal_var=
False)` to 1e-9 over 1, 2 and 3 nodes.

Effect size is reported **before** the p-value, and the SMD is what AN-13's
Table 1 will show non-experts in preference to p-values. A p-value answers
"would this difference be surprising if there were none", which is not the
question a clinician asked.

A small group suppresses the **whole** comparison — releasing the groups that
pass would disclose the one that did not, by difference.

**over_time** — bins are days since each patient's own index event, never
calendar dates. AN-2 has already coarsened the calendar away, and a curve
indexed on wall-clock time would leak the thing the offset exists to remove.
Mismatched bins raise rather than silently producing a plausible curve.

**completeness** — the least glamorous analysis and often the most useful.
Per-patient observation counts leave the node as a **distribution**, not as
per-patient numbers: sending the latter would be sending a per-patient
dataset. `rows_by_author_org` works only because cdr #665 added
`author_org_guid` this afternoon — before that, "who contributed this data"
could only be answered as "who submitted it", a different question. Rows
predating the column are reported as "(not recorded)" with an explanation
rather than being silently folded in.

Heavy missingness is called out in words: if more than half a variable's
values are absent, the result says that any summary of it describes the
patients who happened to be measured.

---

## 2026-09-23 — #654 (AN-11): audit and node policy files

350 tests pass (+10). Gate clean.

**A group run has no single patient**, so `patient_guid` stays NULL and the
run is identified by `spec_hash` — a new indexed column, because "which runs
used this spec" is the question an auditor actually asks. The rest of the run
detail goes in the existing `payload_snapshot` JSONB, which was designed for
exactly this, rather than in five new columns.

Migration `0002_audit_spec`, additive and nullable. Rows before today carry a
`patient_guid` and no `spec_hash`; rows after carry the reverse. **A reader of
this table must not assume either column is always present** — the same
warning ADR-0007 gives.

**Two logs, deliberately.** The platform log, so the run is visible where every
other read is; and a **local log on each node**, so an organisation whose rows
sit in someone else's CDR can still see that its data was used. The second is
the one that is easy to skip and the one that matters most to the
organisations the brief says must remain responsible for their own rows.

**Counts are written suppressed.** An audit log is read by more people than a
result is, and a per-source patient count of 3 discloses as much sitting in an
audit row as it would in a table.

A refused run is still logged — a refusal is a fact about who tried to read
what.

`docs/analyse/node-policy.md` is the operator's reference, and a test loads the
example from it, so the documentation cannot drift into fiction.

---

## 2026-09-23 — #653 (AN-10): synthetic multi-source environment

362 tests pass (+12). Gate clean.

`flask synth --nodes 3 --patients 2000 [--spec …]` builds N sources and drives
the **real** node path on each — policy check, spärr, read, pseudonymise,
project, coarsen, compute, suppress — then merges with the **real**
coordinator. Only the transport is substituted.

This is the first test of the *whole* loop rather than of a layer.
`TestFederatedEqualsPooledOnSyntheticData` re-proves AN-7's property through
node and coordinator rather than on the engine alone, which is a stronger
claim: it would catch a projection or suppression step that silently changed
a number.

**Synthetic ids are deliberately unmistakable.** Patients are `TEST-P…`, a
shape the AN-7 scanner flags on sight, so synthetic data reaching a real
output is caught rather than blending in. Tested that the generator produces
no names, addresses or personnummer-shaped values — the generator must not be
the thing that puts a realistic identifier into a fixture.

**What this does NOT do, stated rather than implied: it does not stand up
containerised CDRs.** A real multi-CDR stack additionally needs one cdr.pdhc
container plus Postgres per source, plan.pdhc reachable for concepts and
units, ips.pdhc reachable for spärr and the consent verdict (both fail closed,
so with ips absent a real node reads nothing at all and the stack is
all-or-nothing), a registered service key per CDR, and seeding — for which
sim.pdhc already generates synthetic longitudinal data from real
PlanDefinitions.

That is most of the platform, which is why it is deployment work rather than a
fixture. The scope is written into `app/testing/synth.py` so it is costed, not
hand-waved. **The harness exercises the code path; a real stack exercises the
wiring, and those are different risks.** AN-0's gap G6 asked for the latter to
be confirmed and it still has not been.

---

## 2026-09-23 — Phase 3: the frontend (#655–#658)

397 tests pass (+35). Gate clean. **ADR-0009: server-rendered, no JavaScript
build chain** (operator decision).

**That choice suits the brief rather than fighting it.** Charts are inline SVG
built on the server, which means: the brief's ban on raw scatters is easy to
hold because binning already happens server-side; suppressed cells render as
`<5` because the renderer knows what was suppressed; WCAG 2.1 AA is simpler
when a chart is markup with real text in it; and nothing can leak client-side
because the client never receives the data, only the picture.

**The method is derived, never chosen.** A user who has to pick between
Pearson and Spearman has already been asked a statistics question, whatever
the button says. Cards are phrased as questions and only *answerable* cards
are offered — offering one that cannot run and failing afterwards is worse
than not offering it, because the user has already committed to the question.

**Sentences come from templates, and that is not a style preference.** A
free-form sentence about a clinical result is a claim nobody reviewed,
produced by something that does not know what it is allowed to say. A template
is read once, argued about once, then trusted. Tests assert that no sentence
in either language contains "effekt", "orsakar", "effect", "causes" or "leads
to", and that effect size precedes any p-value.

**Colour never carries meaning alone** — each series has a distinct dash as
well as a hue, because roughly one man in twelve cannot separate the first two
Okabe-Ito colours and print is often greyscale. A subcohort keeps its colour
across every chart, which is only possible because the server assigns by group
index.

**A share link carries an id and nothing else.** A cohort definition is
potentially identifying — "at this clinic, over 85, with this diagnosis" can
describe one person — and a URL is pasted into chat, logged by proxies and
kept in history. Suppressed cells export as `<k` in CSV too: exporting the
real number "because it is only a CSV" is how a suppressed figure reaches a
spreadsheet and then a slide.

**Phase 3 is NOT accepted.** `docs/analyse/usability-test.md` defines the
five-task walkthrough the brief requires and it **has not been run**. Unit
tests can show a sentence never contains the word "causes"; they cannot show
that a reader does not infer causation anyway. Five participants who match the
target and have not seen the tool being built are what closes this phase.

---

## 2026-09-23 — Phase 4: extensions (#659–#661)

423 tests pass. Gate clean.

**#659 regression and Kaplan-Meier.** Both exactly federatable, which is why
they are here rather than in an approximate bucket: linear returns XᵀX, Xᵀy,
yᵀy and n, which add; Kaplan-Meier returns events and at-risk per interval,
which add. A Hessian is a matrix of sums, not a dataset. Federated equals
pooled to 1e-9. A singular system is **refused rather than guessed** — a
plausible-looking answer from collinear predictors is worse than no answer.

**#660 keyed_hash, gated and limited to counting.** ADR-0010. The line is one
set operation wide: `deduplicated_count` returns a **number** and deliberately
not the union, the overlap or which tokens matched, because a coordinator
holding the overlap could go back to a node and ask about those specific
patients. `join_across_nodes()` exists purely to raise, so the attempt fails
loudly rather than someone assembling the join from a set intersection and
concluding it was permitted because nothing stopped them.

The key is held by the nodes, **never the coordinator** — a coordinator with
it could compute tokens for any identity it guessed and test them against what
the nodes returned, turning a deduplication token into an identity oracle.

**And the honest part: this mode is unusable today.** Gap G9 — the CDR holds
no personnummer. The token would have to come from ips.pdhc, and that path
does not exist. Shipped as a tested mechanism and a documented boundary, not
as a working feature. The legal basis is also not established, and the flag is
where that assertion becomes deliberate.

**#661 differencing hardening.** Three changes, and one is a **behaviour
change**: the default is now **advisory**, not hard-block. A false positive
here blocks legitimate work — two cohorts can differ by four patients for
entirely innocent reasons — and a recorded warning is still evidence if a
pattern emerges. `HARD` is available for an organisation that wants it.

History is keyed by **user and persists across sessions**; keying it by
session would make logging out and back in the whole attack. A cohort
submitted under the **same recipe_id is a rerun, not a probe** — a saved
recipe run monthly will legitimately differ by a patient or two, and treating
that as an attack would make recipes unusable, which is the brief's own
feature. Near misses land in an admin view that carries **no cohort
membership**: a screen about disclosure risk must not itself be a place where
cohorts can be read.

---

## 2026-09-23 — AN-12 (#684): the coordinator↔node wire

459 tests pass (+36). Gate clean. **ADR-0011.**

**What was missing.** Phases 1–4 built a node that computes partials and a
coordinator that merges them, and nothing that carried a partial between two
processes. `app/coordinator/` held `merge.py` and `signing.py` and no client;
there was no node HTTP surface and no transport abstraction. Every test drove
both halves in one process, so "federated" was true of the mathematics and not
of the deployment. `spec-run` reported every source as *"no node configured in
this build"* — the hole was at least honest about itself.

**The acceptance question was parity**, and it is now a test: two nodes served
by real werkzeug servers on real ports, driven over real sockets, produce
`wire.results == local.results` against the in-process harness. If the
transport changed the answer it would be the one thing it must never do.

**The MAC covers the transmitted octets.** The obvious implementation — parse,
re-serialise canonically, compare — makes the signature a property of what the
receiver's parser produced rather than of what the sender sent. The spec
signature may canonicalise, because AN-1 guarantees a spec has exactly one
canonical form; partials carry floats and sketch centroids, which is precisely
where re-serialisation moves bytes. `kind` and `issued_at` are inside the MAC,
so a response cannot be replayed into a request endpoint and a captured
envelope cannot be re-dated.

**Two signatures, two questions.** The envelope says *the other half of this
deployment sent these bytes*; the spec signature says *the coordinator
approved this analysis*. Writing the e2e test surfaced the ambiguity: the node
demanded a spec signature while the coordinator only sent one when given a
separate signing key, so every request 401'd. Resolved by making the signature
**mandatory** and defaulting its secret to the transport secret — a node that
ran unsigned specs whenever the field was absent would be one an attacker
could use by omitting it.

**No patient identifier crosses the boundary.** The coordinator sends a
question; each node resolves which of its own patients it is about. A
coordinator holding guids for every source is the arrangement the design
exists to avoid, and the AN-7 scanner asserts it in `to_json()` output.

**A source that did not answer is named as one** — timeout, refusal,
unreachable, bad envelope, and an unexpected client exception all land in
`failures` and become a degraded `SourceStatus` plus a note. The deliberate
exception: when *no* node answered, `run_distributed` raises rather than
returning a zero-source result, because that result renders as a page of
suppressed cells and reads as a very small cohort rather than as nothing
having run.

### Found while building, not fixed here

- **A node cannot derive a cohort from `spec.cohort`.** `run_spec()` never
  reads the spec's inclusion criteria at all, and the CDR exposes no
  criterion-search or patient-listing endpoint a node could use — its read
  client needs the patient guids it would be meant to produce. Both predate
  this ticket. `ConfiguredCohortSource` (operator pins the cohort per node) is
  what a deployment gets today; the seam is real and the gap is written down
  rather than closed with an invented endpoint. Ticketed.

### Still open

Gap G6 is untouched: this is verified against synthetic CDRs over real
sockets, which exercises the wiring between coordinator and node but not the
wiring to plan.pdhc, ips.pdhc or a deployed CDR.

---

## 2026-09-23 — AN-13 (#696): the cohort criteria are applied

476 tests pass (+17). Gate clean.

**`spec.cohort.include` was read by nothing on the node path.** `run_spec()`
took a list of patient guids and computed over all of them, so a spec saying
"TBSA >= 5" was, at the node, a spec that said nothing — and the figure came
back labelled with a criterion that had never been applied. Not an error: a
*wider population* than the analyst asked about, reported under their
question.

`app/node/cohort_criteria.py` now decides membership, applied in `run_spec`
between coarsening and compute. The candidate list a node starts from is
explicitly candidates; the cohort is derived from it, and the number dropped
is reported as `excluded["cohort"]`.

### Semantics, which are choices

- **A criterion is satisfied if ANY of the patient's observations of that
  concept satisfies it.** "Ever had TBSA >= 5" is how an inclusion criterion
  reads clinically. Testing the *aggregated* value instead would make cohort
  membership depend on the aggregation chosen for an unrelated variable, so
  editing one part of a spec would silently change who is in the study.
- **Criteria are ANDed** — the field is `include`.
- **An unevaluable criterion is an error, never a pass.** An `age_band`
  criterion is refused, because age is not projected into the rows a node
  reads and ignoring it would compute over every age.

### Two bugs found by building it

1. **My own guard was ineffective.** It tested `"concept" in row`, but
   `project()` fills every allowlisted field, so the key is present even when
   the source supplied nothing. The cohort came back *empty* instead of
   raising — which reads as "nobody qualifies" rather than "this node cannot
   tell". Now tests the value.
2. **The synthetic harness had no `concept` field at all.** That is part of
   why nothing noticed `spec.cohort` was unused: there was nothing for a
   criterion to match against. `synth.build()` now emits it.

Also fixed a flaky assertion I wrote in the CLI tests — it asserted source
order from a *concurrent* fan-out. The gate caught it.

### Still open, and now an efficiency question rather than a correctness one

A node reads its candidates' observations and decides membership afterwards.
Narrowing the read itself needs the CDR to answer "which of your patients
match this"; its FHIR search supports `code` and `date` but **no
`value-quantity`**, so a value predicate cannot be pushed down. Specified for
cdr.pdhc separately. `ConfiguredCohortSource` still supplies candidates.

---

## 2026-09-24 — AN-14 (#701) is blocked on a judgement, not on code

Investigated before starting the push-down and stopped. Recording the finding
so the next person does not re-derive it.

**The push-down cannot honour "spärr before read" as things stand.**

`run_spec`'s ordering — spärr excludes blocked patients *before* any read — is
the security argument, not a detail: an aggregate computed over a blocked
patient has used their data even if the number is discarded. A
candidate-narrowing query is itself a read.

Three facts establish the problem:

1. **cdr's FHIR search applies consent but NOT spärr.** `fhir_read.search()`
   runs `_consent_filter` (#422) and `check_patient_allowed`; nothing on that
   path consults ips blocks. Spärr lives on the node
   (`reader.excluded_by_spärr`).
2. So a candidate query returns **blocked patients' rows to the node**, before
   the node has excluded them.
3. The obvious workaround — send an allow-list of already-spärr-filtered
   patients — does not work either: `search()` reads `patient` with
   `request.args.get`, singular, so one patient per query.

### The fork, which is not mine to pick

- **(a)** Teach cdr's search path to apply spärr. A cdr change, and it would
  need ips reachable on every search — which changes that endpoint's failure
  mode platform-wide.
- **(b)** Accept that a candidate query reads blocked patients and discards
  them. That is precisely what the codebase refuses to do for aggregates, so
  it is a DPO-level judgement, not an engineering preference.
- **(c)** Support a repeated `patient=` (or a `_has`-style patient filter) so
  the node can pass a spärr-filtered allow-list. Smallest cdr change; keeps
  the ordering intact.
- **(d)** Leave it. `ConfiguredCohortSource` stays, the node reads its
  candidates and filters locally, and #698's `value-quantity` serves other
  FHIR clients rather than the node.

**(c) looks right to me** — it preserves the ordering rather than arguing
about it, and it is a small, well-understood change. But it is still a cdr
change plus a consent/ordering call, so it is being raised rather than taken.

Nothing about #696 changes either way: cohort criteria are applied at the
node today and the result is correct, just not cheap.

---

## 2026-09-24 — #687: keyed_hash linkage retired

467 tests pass (−9, the keyed_hash tests went with the mode). Gate clean.

Operator decision: **retire the mode, keep ADR-0010 as the record.**

`keyed_hash` is removed from the `Linkage` enum, `app/privacy/linkage.py` and
its tests are deleted, and the committed JSON schema is regenerated — the
schema now offers `shared_guid` and `none` only, so a spec naming
`keyed_hash` fails validation rather than being accepted and then refused.

**Why, restated because the code no longer says it.** It shipped as a tested
mechanism that could not be used: the CDR holds no personnummer, the ips
token path was never specified, and the legal basis was never established.
Keeping it behind a flag meant a control that *looked* like a feature, and
"shipped but unusable" is how something gets switched on by someone who never
read the ADR. That was the argument for removing it rather than leaving it
dormant.

**ADR-0010 is not deleted — it is marked RETIRED and carries the design
forward**, in particular the one thing a reviver must not lose: the key is
held by the nodes and **never** the coordinator. A coordinator holding it
could compute tokens for any identity it guessed and test them against what
the nodes returned, turning a deduplication token into an identity oracle.
Reviving it needs all three of an ips-side token path, a documented legal
basis, and that design honoured.

Nothing in the application imported the module — only the tests did — so
removal touched no live path.

---

## 2026-09-24 — #700: decided — leave it, revisit after #685

Operator decision. The federated endpoints (#291 gateway proxy, #292 monitor)
keep today's behaviour: they neither join consent locally nor declare a
purpose, so the CDRs pass them through.

**Deliberate, and the reasoning is worth keeping.** The reconstruction is not
deployed, and its node reader — which *does* declare a purpose — is the
intended consumer of the gates built in #664 and #699. Settling the policy
before that path is live would be deciding about traffic that is about to
change shape.

**What this leaves open, stated plainly so it is not mistaken for closed:**
on the currently-live federated path neither side applies the analysis
consent join. Spärr is unaffected — blocking is applied unconditionally on
every CDR. What passes unfiltered is the analysis opt-out and per-project
research consent.

**#700 stays OPEN** as the reminder, and #685 should not be considered done
until it is revisited.

## #685 — reconstruction DEPLOYED (2026-09-29)

Release `2026-09-29T19-00-32Z`. Verified in the container: SQLAlchemy 2.0.54,
`flask db current` = `0002_audit_spec (head)`, `ANALYSE_ROLE=coordinator`,
5 CDR_ENDPOINTS intact, 11 engine modules present, **0 `/api/v1/node/*` routes**,
`analyse.pdhc.se/healthz` 200.

### The divergence survey found the opposite of what was feared

The ticket warned a wholesale deploy would drop the deployed-only #540/#541
wiring. It would not have: I pulled the prod tree and asked git whether it had
ever seen each file's contents — **44 of 45 known, the sole exception `.env`**,
which is gitignored by design. 40 of 44 files were byte-identical to HEAD; the
4 that differed were simply older git states. **No on-server-only code edits
existed.** The wiring lives entirely in `.env` — which does live in the release
directory, not `shared/`, so carrying it forward was the one step that mattered.

### The deploy was one line of config, not six containers

I initially proposed standing up six node containers. That was wrong, and the
operator pushed back. The live analysis path uses `CdrRegistry` + `fanout` via
`CDR_ENDPOINTS` (already wired to cdr2–6 since #541); the AN-12
coordinator↔node transport is reached only from `cli_analyse.py` and no web
route dispatches to it. Nodes are for when the data exists — now #712.

`ANALYSE_ROLE=coordinator` mattered for a second reason: unset defaults to
`"both"`, which registers the node surface on the public instance. The code
says why — *"a coordinator that also served /api/v1/node/run would be a
second, quieter way into the data, reachable by anyone holding the transport
secret."*

### It crash-looped first, and the cause is platform-wide (#711)

```
ModuleNotFoundError: No module named 'psycopg'
```

**SQLAlchemy 2.1 changed the default driver for a bare `postgresql://` from
psycopg2 to psycopg v3.** `requirements.txt` said `SQLAlchemy>=2.0`, so the
2026-09-23 image resolved 2.0.51 and a fresh build resolved 2.1.1.

Rolled back within ~90 seconds (symlink flip + rebuild), service restored on
0.2.0-reform, then diagnosed. Fixed with two independent guards: the pin
**and** an explicit `postgresql+psycopg2://` in compose.

**467 tests passed throughout**, because the local venv still holds 2.0.51.
Only a from-scratch image build can see it. And the rollback worked only
because its unchanged `requirements.txt` hit the Docker layer cache — the
cache is the sole thing hiding this across the platform.

**14 of 21 running services still use a bare URL**, sso.pdhc among them. Filed
as **#711**. sso was rebuilt earlier the same day and survived only on a cache
hit; a rebuild after any requirements edit would have taken down platform
authentication.

### Still outstanding

`#688` — the SSO-gated usability walkthrough was #685's acceptance and needs a
human with a real professional token. It has not been done.

## #709 — the dead-end triage, after the deploy (2026-09-30)

#704 item 5 was blocked until #685 landed, because prod was 0.2.0-reform while
local was the rebuild and something might have been reachable in one and not
the other. Prod is now HEAD, so that ambiguity is gone.

Re-ran the detector: **53 candidates**. Classified every one by where it is
actually referenced (app code, tests, templates) rather than by name alone:

| | |
|---|---|
| 17 | **live** — referenced in app code; detector false positives |
| 26 | **tests only** |
| 10 | **no reference anywhere** |

The value was not in the deletions. It was in what the "tests only" group
turned out to be.

### Two capabilities built, tested, and wired to nothing

**#713 — 5 of 9 analysis types can never produce a result.** `REGISTRY` holds
nine kinds; `node/runner.py::_dispatch` has an if-chain handling four.
`compare_groups`, `completeness`, `kaplan_meier`, `linear_regression` and
`over_time` fall through to `return None`, and line 141 skips a `None` partial
**without appending a note**. A researcher asking for a comparison gets a run
with no partial for it and no explanation. `regression.py` is the clearest
case: its triple is named per model precisely so one module can serve two
kinds — and the branch that would call it was never written, while all six of
its functions carry 4–8 test references each.

**#714 — `app/ui/` is a complete presentation layer with no surface.** Six
modules, covered by `tests/test_ui.py`, imported by nothing outside the
package. The sentence builders look like exactly the plain-language rendering
the brief asks for.

Both are the #704 class-A pattern, and neither is dead code.

### Deleted — genuine #663 residue

`#663` removed individual-patient analysis (it moved to dashboard.pdhc) and
left the services behind:

- `app/services/patient_directory.py` — 129 lines, imported by nothing, no
  tests.
- `auth.load_user` — a no-op whose docstring said it was "kept for back-compat
  with existing route imports", where no such import exists.
- `role_guards.admin_required` and `.clinical_required` — unused;
  `researcher_required` and `nurse_required` stay.

467 tests still pass. Detector down from 53 to 48.

### Deliberately NOT deleted

`ips_client`'s block helpers — `filter_blocked_rows`, `filter_blocked_points`,
`has_any_active_block`, `check_source_blocked`, `patient_has_block`,
`get_active_blocks` — are the pre-#663 spärr path and now have no caller.

**Spärr is still enforced, by a different route**: `node/reader.excluded_by_spärr`
calls ips `/api/v1/blocks/check-bulk` from `node/runner.py:85`, excludes blocked
patients *before* any computation, and **fails closed** — if ips cannot answer,
every patient counts as blocked. Verified, not assumed.

Removing superseded safety code is a deliberate act for whoever owns the spärr
model, not a tidy-up. A note now sits in `ips_client.py`'s module docstring
saying so, so the next sweep reads the reasoning instead of re-raising them.

## #715 — the node dispatch never selected rows by variable (2026-09-30)

Found while implementing #713, which I had filed with the wrong diagnosis.

Rows arrive **long** — one per observation, `concept` naming the variable —
because `projection.py` is explicit that "the concept selects which rows, it
is not a field of its own". Every engine expects **wide** per-patient records:
`completeness` reads `r.get(variable_name)`, `correlation` takes
`columns: dict[name, values]`, `compare_groups` takes `groups: dict[name,
values]`. **Nothing bridged the two.**

Measured before the fix, on a dataset where variable `a` had 10 rows (0–9) and
`b` had 10 (0–18):

| analysis | was | should be |
|---|---|---|
| `describe(vars=["a"])` | **n = 20**, sum 135 | n = 10, sum 45 |
| `frequency(vars=["a"])` | counts ran past 9 into b's range | ten counts of 1 |
| `histogram(var="a")` | 20 rows binned | 10 |
| `correlation(["a","b"])` | **n = 0**, all sums 0.0 | n = 10 |
| compare_groups / completeness / over_time | `None`, silently | a partial |

`correlation` was the worst: a well-formed Partial over nothing, which the
coordinator merges and finalizes into something a researcher reads as real.

### The fix — a pivot, not new semantics

`app/node/frame.py` builds one wide record per patient. **The collapse rules
were already specified and are not invented here:** `Agg`'s own docstring is
"how repeated observations collapse to one value per patient", `agg` is
mandatory for an observation series and forbidden for `demographics.*` /
`meta.*`, and `completeness` defines a patient with no qualifying observation
as *present with a null* — the only way absence can be measured at all.

Four choices the spec left open, all decided toward "absence is None", which
composes with what `completeness` already counts:

- `count` of nothing is **0** — a count of nothing really is zero.
- `slope` with fewer than two points is **None** — undefined.
- `time_to_first_event` with no event is **None**. Treating it as day 0 would
  put every never-affected patient at the far left of a survival curve.
- `window_days` is **inclusive at both ends** — "days 0–30" is how a clinician
  writes it.

`over_time` is the one engine that keeps the long rows: it plots observations
against time, so collapsing per patient first would destroy the thing drawn.

### No more silent skips

Every path that yields no partial now appends a note: an unimplemented type, a
`DispatchError` with its reason, or a bare `None`. A missing number with no
explanation is worse than an error — the researcher cannot tell a suppressed
result from one that was never computed.

Group membership treats a record missing the predicate's variable as **not a
member**. Absence is not a failed comparison; the other reading would move
every incompletely-recorded patient into the opposite group silently.

### 482 tests (was 467)

`tests/test_frame.py` asserts the defect in the exact shape it took, plus the
invariant: **every type the spec can express either produces a partial or an
explanation.** Verified to have teeth — removing one dispatch branch makes it
fail.

Worth noting all 467 tests passed before *and* after the fix. None of them
exercised the dispatch with more than one concept, which is precisely why this
survived.

### Still open

`linear_regression` and `kaplan_meier` are in the engine REGISTRY but **not in
the spec's `Analysis` union**, so no spec can request them. Ahead of the spec
rather than broken; exposing them is a separate decision.

## linear_regression / kaplan_meier marked NOT FULLY DEPLOYED (2026-09-30)

Operator asked for these to be recorded rather than built, so they are not
forgotten. Markers are in `app/engine/__init__.py` beside the REGISTRY entries
and at the top of `app/engine/regression.py`, plus `newtask.txt`.

They are implemented, tested and registered, and **unreachable**: the spec's
`Analysis` union has no member of either type, so no spec can ask for one.
Ahead of the spec, not broken. Exposing them needs decisions nobody has taken —
what a linear regression declares as predictors vs outcome, how a survival spec
expresses its event and censoring — and #712 should not stand up nodes first.

Recorded in the code rather than only in a ticket because the ticket API's
`/respond` auto-closes and there is no reopen, so #712 cannot be appended to
without closing it.

### A gotcha that cost twenty minutes

Verifying the #715 invariant test "has teeth" meant temporarily disabling one
dispatch branch: `"completeness"` → `"__disabled__"`. The test duly failed, the
file was restored, and the suite then failed for real — with source that was
demonstrably correct and a REGISTRY containing every key.

**Python was serving stale bytecode.** A `.pyc` is validated against the
source's *size and mtime*. `"completeness"` and `"__disabled__"` are the same
length, and both writes landed inside the same second, so the cache looked
valid and kept the disabled version. `inspect.getsource` reads the *file*, so
it showed the correct code while the wrong code ran — which is what made it
confusing.

Clearing `__pycache__` restored 482 passing. If a same-length edit is ever used
to prove a test fails, delete the caches afterwards.

## #708 — the first sibling smoke, and what it found on its first run

`deploy/smoke_siblings.py`, run inside the container because that is the only
place the real service keys and network names exist:

```
docker exec analyse_pdhc_app python deploy/smoke_siblings.py
```

Read-only, so it is safe against production at any time, including straight
after a deploy. `--json` for machine output; exit code 0 only if everything
passed.

**It drives the app's own clients** — `CdrRegistry`, `fanout`, `NodeReader` —
rather than hand-written `curl`. That is the whole design point. Every
cross-service defect in the 2026-09-29/30 sweep was a mismatch between what a
client sent and what the sibling accepted (#710 a bearer the caller could not
hold, #712 a response shape that crashed the parser, #713 a key named
`organisation_guid` where the client read `guid`). A smoke built from my idea
of the contract would have passed through all of them.

It also uses **production's own `CDR_FANOUT_TIMEOUT`**, not a value chosen to
make it pass. If a fanout cannot finish inside what the live path allows, that
is the finding.

### Two real findings on the first run

**#717 — the node spärr gate calls an ips endpoint that does not exist.**
`excluded_by_spärr` POSTs to `/api/v1/blocks/check-bulk`; ips has no such
route, and no bulk endpoint at all. The real one is
`GET /api/v1/patients/<guid>/blocks/check`, per patient. It 404s, the gate
fails **closed** — correct, and exactly what its docstring promises — so no
data leaks, but a node excludes *every* patient every time. #715 fixed the
dispatch so analyses compute over the right rows; this means no rows would
ever reach them. The platform memory already had the answer: "use
/blocks/check for cross-service spärr". The bulk variant was assumed.

**#718 — cdr2–5 take ~7.5s to answer `_count=1` on `/api/v1/fhir/Observation`**
while `/api/v1/stats` answers in ~100ms and cdr6 answers in 6ms. Five parallel
queries exceed the live 15s fanout timeout, so `observations_search` — a live
analyse surface — is degraded against cdr2–5 today.

### Two of my own mistakes, worth recording

Both were caught by running it, which is the argument for the whole exercise:

- I wrote `path="fhir/Observation"`; the live code uses
  `/api/v1/fhir/Observation`. A smoke that invents the path tests nothing.
- `FanoutResponse.failed` is a list of id **strings**, and `NodeReader` takes
  `base_url`/`service_key`, not `cdr_base_url`. I guessed both signatures.

The script also needed `sys.path` derived from `__file__` rather than the
working directory — the same trap as the hash-stability test fixed the same
morning.

## #717 — the spärr gate called an endpoint that never existed (2026-09-30)

`excluded_by_spärr` POSTed to `{ips}/api/v1/blocks/check-bulk`. ips has **no
bulk endpoint at all**. Every call 404'd, the gate failed closed on every run,
and a node could never return a single row — #715 had just fixed the dispatch
so analyses compute over the right rows, and none would ever have reached them.

### Two mistakes, and request.pdhc had made both before

Its `ips_client` docstring records them: the wrong endpoint, and a header ips
ignores — `require_auth` reads **only** `Authorization`, so `X-API-Key` is
silently dropped. request.pdhc's version therefore failed **OPEN**: no
ServiceRequest was ever hidden. analyse's failed **CLOSED**. Opposite
directions, same root cause, and for analysis closed is right — an aggregate
computed over a blocked patient has already used their data even if the number
is thrown away.

The real predicate, mirrored from the working caller rather than guessed:

```
GET /api/v1/patients/<guid>/blocks/check?source_clinic_id=<org>
Authorization: ApiKey <key>
```

### Spärr is per SOURCE, which the node could not express

ips answers "is data from source X readable for patient P". `NodePolicy` had
no organisation identity at all, so the question was unaskable. Added
`source_clinic_id` — and the policy file is exactly where it belongs, since
ADR-0005 says that file is owned by the organisation behind the CDR.

`404` means the patient is unknown to ips and therefore genuinely unblocked,
not an outage — the same reading request.pdhc takes. Anything else fails
closed. All three of `IPS_BASE_URL`, `source_clinic_id` and `IPS_API_KEY`
refuse with a message naming what is missing, rather than returning "no blocks
found" — a gate that silently stops gating is the failure being guarded
against.

Patients are checked in parallel, bounded at 8 workers: ips answers one at a
time and a cohort is many, but an unbounded pool against a sibling is a denial
of service with extra steps.

493 tests (was 482). `tests/test_sparr_gate.py` asserts the contract — URL,
params, `Authorization: ApiKey`, and that `X-API-Key` is *not* sent.

### OPERATOR STEP — this is not finished without it

**analyse has no `IPS_API_KEY`.** request.pdhc has one; analyse was never
issued one. Until an ips ApiKey is minted for analyse and added to its `.env`,
the gate keeps failing closed — safely, and now with a message that says
exactly which of the three things is missing instead of a bare 404.

The same applies to `source_clinic_id`: every node policy file needs one
before that node can run.

## Ticket #714 — app/ui/ has no route, and that blocks #688 (2026-09-30)

#714 asked whether the six-module `app/ui/` package should be wired in or
recorded as held back. Investigating produced a third answer.

### It is neither orphaned nor superseded

`app/ui/` renders **engine output**: the sentence builders take
`Result.pooled`, `exactness` and the disclosure `k_min`; `make_recipe` takes an
`AnalysisSpec`; `aggregates_csv` and `provenance_rows` take a result dict.
Those shapes come from `app/coordinator/`, and **the coordinator has no web
route** — it is reached only from `cli_analyse.py`.

`app/routes/researcher.py` is a *different* surface with different inputs: its
`cohort_histogram` / `boxplot` / `scatter` / `trend` endpoints read rows fanned
out through `CdrRegistry`. The two are not interchangeable. Wiring this package
into that route would hand it a shape it does not accept — so "just import it
somewhere" was never available.

### The consequence nobody had noticed

**#688 is the Phase 3 usability acceptance for these exact modules**
(#655–#658). It says it follows the deploy ticket, and #685 is now done — so
the next person to pick up #688 would have found there is nothing to walk a
user through. Its four claims (cards as questions, method derived not chosen,
suppressed cells reading as protected, SVG verified with a screen reader) all
require a rendered page.

Filed as **#722**, and #688 is blocked on it.

### Recorded where it will be read

The reasoning now lives in `app/ui/__init__.py`, not only in a ticket, so the
next dead-code sweep reads it instead of re-reporting fifteen functions — the
same treatment as `require_organisation` in sso (#707) and the superseded
spärr helpers in `ips_client` (#709).

493 tests unchanged; this is documentation, not behaviour.

## Ticket #722 — the coordinator now has a web surface (2026-09-30)

`app/ui/` (Phase 3, #655–#658) was built, tested and reachable by nothing,
because it renders engine output and the coordinator had only a CLI. #714
found that; this is the page.

`GET /analysis` and `POST /analysis/run`, plus an aggregates-only CSV export,
all behind `researcher_required` like the cohort routes.

### Two paths, and the page always says which

- **Configured nodes** — when `ANALYSE_NODES` *and* `ANALYSE_TRANSPORT_SECRET`
  are both set, the spec is fanned out for real.
- **Synthetic** — otherwise. `app/testing/synth` drives the **real** node and
  coordinator code over generated data; it is not a mock of the engine, it is
  the engine over data nobody's health depends on.

Synthetic is the honest default rather than an error page: the brief makes
`data_mode: synthetic` the default everywhere, and #688's questions — are the
cards answerable, is the method derived not chosen, does `<5` read as
protected — need a rendered page, not real patients.

**Both conditions are required for "live".** Nodes alone do not count, because
the transport secret has no default by design: one that fell back to a
development constant would ship working and attest nothing.

### The property that mattered most, and a real gap it exposed

A synthetic run must never be mistaken for a real one. The page carries a
banner — but a banner can be screenshotted away from its numbers, so the mode
belongs in the **provenance**, which travels into the CSV and any report.

`provenance_rows()` mapped known keys to labels and **silently dropped
unknown ones**, so `data_mode` never reached the reader. Fixed in
`app/ui/recipes.py`: "Data" is now the *first* provenance row and spells the
value out — "SYNTHETIC — generated data, no real patients" — rather than
passing a codeword through.

### One thing that would have made a poor first impression

The example spec on the page originally used `systolic`; the synthetic
generator writes `concept: "x"`. It validated, ran, and returned **nothing** —
a page whose own example produces no answer teaches the reader that the tool
does not work. The example now uses the synthetic vocabulary, with a comment
saying why.

### Rendering failures do not lose the numbers

A sentence template that raises is caught per block, so correct figures are
not discarded for a cosmetic reason. Asserted by a test that makes
`describe_sentence` throw.

503 tests (was 493). **#688 is now performable** — it was not before.

## 2026-09-30 — #688 walkthrough: suppression specs, and two defects behind them

Asked for a spec that forces suppression, since every shipped example returns
comfortable numbers and nothing on the page ever crossed `k_min`. Two specs are
now in `docs/walkthrough_688/`, verified against the synthetic population.

Building them found two things.

**#723 — histogram bins below `k_min` could reach the page. FIXED, NOT DEPLOYED.**
A `width=2` histogram published a first bin of 2 patients against `k_min=5`.
`merge_small_bins` folded a small bin into `out[-1]` only, so the first bin —
appended while `out` was empty — went out at its raw count whenever the bin
after it was large enough not to merge. A property test over 500 random bin
sets then found a second case: a zero bin is exempt from merging (publishing
"no patients" discloses nobody) but was still eligible to be merged *into*, and
folding 2 into 0 leaves 2 on the page. Replaced with a converging sweep; a lone
bin below `k_min` now becomes `SUPPRESSED` rather than a number. 510 tests pass.
The docstring had promised "no patient is lost at either end" — one end was
implemented.

The two existing tests covered a middle bin and a trailing bin. Neither end
case was covered, which is exactly why neither was caught; the new test asserts
the guarantee over random input rather than adding a third example.

**#724 — the page renders 2 of 7 analysis kinds.** `_render_result` handles
`describe` and `histogram`. `frequency` — the kind that demonstrates
suppression — renders as a card with its heading, no counts, and the single
line "Siffrorna är exakta även när flera källor kombineras". On the card where
a category was withheld, the only sentence tells the reader the figures are
exact and shows none. `sentences.suppression_sentence(k_min, lang)` exists,
tested, with no call site outside `app/ui/`. So do `comparison_sentence`,
`linkage_sentence`, `charts.data_table`, `heatmap` and `curve`.

This is #714 one layer in: #714 found `app/ui` had no route, #722 gave it one
and wired two kinds. #688's usability question cannot be answered until #724 is
done — the walkthrough would be assessing a page that does not show its most
important output.

**Open for the operator:** #723 is committed locally and not deployed. It is a
disclosure fix on a live surface; deploying is a state change on the mini that
was not part of the greenlit task.
