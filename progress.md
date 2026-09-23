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
