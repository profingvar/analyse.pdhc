# changed_files.md — analyse.pdhc (append-only, Rule 17)

## 2026-08-07 — #538 scaffold + #539 port (new service, all files created)

### Bookkeeping / root
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/readme.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/progress.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/newtask.txt
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/changed_files.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/start.sh
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/docs/technical.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/docs/user_manual.md

### Container / packaging
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/Dockerfile
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/docker-compose.yml
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/entrypoint.sh
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/wsgi.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/requirements.txt
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.env.example
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.dockerignore
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.gitignore

### App package
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/extensions.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/version.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/auth.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/models/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/models/audit.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/audit.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/ips_client.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/role_guards.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/session_headers.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/federation.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/aggregations.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/cohort.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/observations_search.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/stats.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/canonical.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/openehr.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/api/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/api/health.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/auth.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/researcher.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/views.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/templates/researcher_workspace.html

### Migrations
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/alembic.ini
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/env.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/script.py.mako
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/versions/0001_initial.py

### Tests
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/conftest.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_health.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_researcher_flow.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_analyse_aux.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_auth_gate.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_sso_web.py
2026-08-07T18:03:33Z docs/cutover_runbook.md — cross-service deploy+cutover runbook (#540/#541/#542/#543/#547/#544)
- analyse.pdhc/docs/technical.md (Port Allocation section)

## 2026-09-02T15:05:46Z — #578 Stage 1: org-scoped clinical patient list (choose patient)
- analyse_app/app/services/patient_directory.py (new) — ips clinic-patients + demographics
- analyse_app/app/analyse/patient_list.py (new) — org-scoped, CDR-enriched, spärr-aware list
- analyse_app/app/routes/clinical.py (new) — GET /api/cdrs, GET /api/patients
- analyse_app/app/services/role_guards.py — clinical_required (care roles + admin)
- analyse_app/app/routes/views.py — landing→choose_patient; /researcher for the workspace
- analyse_app/app/__init__.py — register clinical_bp
- analyse_app/app/templates/choose_patient.html (new) — nav + CDR selector + patient table
- analyse_app/tests/{test_patient_list,test_clinical_api}.py (new); test_sso_web.py updated

## 2026-09-03T00:00:00Z — #579 Stage 2: per-patient dashboard + spärr enforcement + admin log
- analyse_app/app/analyse/patient_detail.py (new) — per-patient series assembler, spärr enforced on data
- analyse_app/app/routes/clinical.py — GET /api/patient/<guid>, GET /api/admin/sparr-log; audit exposure/hidden
- analyse_app/app/routes/views.py — /patient/<guid> shell, /admin/sparr-log (admin_required)
- analyse_app/app/templates/patient_detail.html (new) — demographics, spärr banner, CDR picker, per-series sparklines
- analyse_app/app/templates/sparr_log.html (new) — admin oversight over sparr_lift_exposure/sparr_hidden
- analyse_app/app/templates/choose_patient.html — Spärrlogg nav link
- analyse_app/tests/test_patient_detail.py (new); test_clinical_api.py extended (patient + admin-log + exposure-logged)

## 2026-09-03T07:00:00Z — #579 item-3: live-shape verification + fixes
- analyse_app/app/routes/clinical.py — _bearer() now reads session["sso_token"] (ips rejects service key)
- analyse_app/app/services/patient_directory.py — _name_of handles flat family_name/given_name (ips clinics/patients)
- analyse_app/app/analyse/patient_detail.py — series keyed by plan.pdhc Concept guid (coding[1]); per-clinic spärr via meta.security org_guid (replaces coarse hide); admin break-glass exposes+logs exposed_orgs
- analyse_app/app/templates/patient_detail.html — banner reflects partial hide / exposure / block-present / ips-unavailable
- analyse_app/tests/test_patient_detail.py — rewritten for per-clinic model + concept-guid grouping
- analyse_app/tests/test_clinical_api.py — _obsc carries concept coding + org; exposure test uses a clinic block
- analyse_app/tests/test_patient_directory.py (new) — locks verified ips flat + FHIR name shapes

## 2026-09-03T08:00:00Z — #579 item-3 (2): spärr via /blocks/check (fail-open fix)
- analyse_app/app/services/ips_client.py — check_source_blocked + patient_has_block (relationship-free)
- analyse_app/app/analyse/patient_detail.py — block_check(org) callable; per-producing-org filter; drop /blocks-list use
- analyse_app/app/routes/clinical.py — _source_block_check memoised; list badge via patient_has_block (metadata)
- analyse_app/app/templates/patient_detail.html — drop dead "unavailable" banner branch
- analyse_app/tests/test_patient_detail.py — block_check model; add fail-safe-on-None test
- analyse_app/tests/test_clinical_api.py — exposure test patches check_source_blocked
- analyse_app/tests/test_ips_blocks.py (new) — lock /blocks/check + /blocks/metadata contract

## 2026-09-03T06:43:00Z — #579 item 5: reform DEPLOYED to analyse.pdhc
- analyse_app/app/version.py — 0.1.0-scaffold -> 0.2.0-reform (deploy marker)
- SERVER: new release /usr/local/www/analyse.pdhc/releases/2026-09-03T06-42-49Z
  (rsync of HEAD analyse_app/, .env preserved, docker-compose up -d --build,
  image analyse_pdhc-app rebuilt, /healthz=0.2.0-reform). Predeploy backup in
  ~/backups/predeploy/analyse.pdhc/.

## 2026-09-23 — #644 AN-1: analysis spec
- analyse_app/app/spec/models.py (new) — Pydantic models, platform purpose enum
- analyse_app/app/spec/digest.py (new) — canonicalisation, spec_hash, provenance
- analyse_app/app/spec/schema.py (new) — JSON Schema generated from the models
- analyse_app/app/spec/__init__.py (new)
- analyse_app/app/cli.py — `flask spec-schema` regeneration command
- analyse_app/app/__init__.py — register the spec CLI
- analyse_app/requirements.txt — pydantic>=2.7
- analyse_app/tests/test_spec.py (new, 32)
- docs/analyse/analysis-spec-v1.schema.json (new, generated artefact)

## 2026-09-23 — #645 AN-2: privacy layer
- analyse_app/app/privacy/projection.py (new) — constructive allowlist projection
- analyse_app/app/privacy/pseudonym.py (new) — per-project HMAC pseudonyms, ProjectKey
- analyse_app/app/privacy/coarsen.py (new) — day offsets, age bands, calendar grain
- analyse_app/app/privacy/__init__.py (new)
- analyse_app/tests/test_privacy.py (new, 57)

## 2026-09-23 — #646 AN-3: disclosure control
- analyse_app/app/privacy/disclosure.py (new) — k_min policy, primary+secondary suppression, safe summaries, differencing guard
- analyse_app/app/privacy/__init__.py — export disclosure
- analyse_app/tests/test_disclosure.py (new, 64 incl. 40 property tests)

## 2026-09-23 — #647 AN-4: engine part 1
- analyse_app/app/engine/base.py (new) — Partial/Result, the three-function contract
- analyse_app/app/engine/sketch.py (new) — t-digest + protect() against singleton centroids
- analyse_app/app/engine/describe.py (new)
- analyse_app/app/engine/histogram.py (new)
- analyse_app/app/engine/frequency.py (new)
- analyse_app/app/engine/correlation.py (new)
- analyse_app/app/engine/__init__.py (new) — REGISTRY
- analyse_app/requirements.txt — scipy>=1.14
- analyse_app/tests/test_engine.py (new, 30)
- docs/analyse/decisions/0008-no-dataframe-engine-yet.md (new)

## 2026-09-23 — #648 AN-5: node service
- analyse_app/app/node/policy.py (new) — NodePolicy, owned by the CDR's organisation
- analyse_app/app/node/reader.py (new) — read client, declares purpose, fails closed
- analyse_app/app/node/runner.py (new) — the ordered execution path
- analyse_app/app/node/__init__.py (new)
- analyse_app/requirements.txt — PyYAML
- analyse_app/tests/test_node.py (new, 22)

## 2026-09-23 — #650 AN-7: test harness + identifier gate
- analyse_app/app/testing/scanner.py (new) — identifier scanner
- analyse_app/app/testing/__init__.py (new)
- analyse_app/scripts/gate.sh (new) — runs the suite then scans its output; exit 1 on a hit
- analyse_app/tests/test_harness.py (new, 47)

## 2026-09-23 — #651 AN-8 coordinator, #649 AN-6 CLI
- analyse_app/app/coordinator/signing.py (new) — HMAC over the canonical form
- analyse_app/app/coordinator/merge.py (new) — fan-in, degradation, pooling rules
- analyse_app/app/coordinator/__init__.py (new)
- analyse_app/app/cli_analyse.py (new) — spec-validate / spec-canonical / spec-run / sources-list
- analyse_app/app/__init__.py — register the analyse CLI
- analyse_app/tests/test_coordinator.py (new, 13)
- analyse_app/tests/test_cli_analyse.py (new, 12)

## 2026-09-23 — #652 AN-9: engine part 2
- analyse_app/app/engine/compare_groups.py (new)
- analyse_app/app/engine/over_time.py (new)
- analyse_app/app/engine/completeness.py (new)
- analyse_app/app/engine/__init__.py — registry extended to 7 kinds
- analyse_app/tests/test_engine2.py (new, 20)

## 2026-09-23 — #654 AN-11: audit and node policy files
- analyse_app/app/models/audit.py — spec_hash column
- analyse_app/migrations/versions/0002_audit_spec_hash.py (new)
- analyse_app/app/services/run_audit.py (new) — dual log, suppressed counts
- analyse_app/tests/test_run_audit.py (new, 10)
- docs/analyse/node-policy.md (new) — the operator's reference

## 2026-09-23 — #653 AN-10: synthetic multi-source environment
- analyse_app/app/testing/synth.py (new) — N sources through the real node + coordinator path
- analyse_app/app/testing/__init__.py — export synth
- analyse_app/app/cli_analyse.py — `flask synth`
- analyse_app/tests/test_synth.py (new, 12)

## 2026-09-23 — Phase 3 (#655 AN-12, #656 AN-13, #657 AN-14, #658 AN-15)
- analyse_app/app/ui/palette.py (new) — Okabe-Ito, colour never alone
- analyse_app/app/ui/charts.py (new) — inline SVG, server-rendered
- analyse_app/app/ui/questions.py (new) — question cards, method derived
- analyse_app/app/ui/sentences.py (new) — templated summaries, sv/en
- analyse_app/app/ui/i18n.py (new) — Swedish default
- analyse_app/app/ui/recipes.py (new) — recipes, CSV export, provenance rows
- analyse_app/app/ui/__init__.py (new)
- analyse_app/tests/test_ui.py (new, 35)
- docs/analyse/usability-test.md (new) — the walkthrough, NOT YET RUN
- docs/analyse/decisions/0009-server-rendered-frontend.md (new)
