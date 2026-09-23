# analyse.pdhc — Phase 0 discovery

Ticket #642 (AN-0) · Written 2026-09-23 · No feature code was written.

Scope of reading authorised by [ADR-0002](decisions/0002-sibling-repo-reads.md):
sibling repos read-only. Repos inspected: `cdr.pdhc`, `ips.pdhc`,
`analyse.pdhc`, plus a read-only check on `miserver`.

**Headline.** The consent and purpose infrastructure the brief asks for largely
**already exists and is better than the brief assumes** — ips owns a bulk,
purpose-aware, fail-closed verdict service. The real gap is narrower and
sharper than "build consent": a *machine* caller currently **bypasses** that
gate by design, and analyse compensates in application code under a
hard-coded purpose. §4 and §7 are the parts to read if you read nothing else.

---

## 1. How observations are modelled

`cdr.pdhc` holds **three** stores, not one:

| Store | Table | Shape |
|---|---|---|
| Flat metrics | `health_observations` | `patient_guid`, `metric`, `value` (Numeric 12,4), `unit`, `source_type`, `source_code`, `source_service`, **`concept_guid`** (indexed), `effective_at`, `received_at` |
| FHIR per-type | dynamic, one table per resource type + a `*_history` twin | `guid`, `patient_guid`, **`org_guid`**, **`code_canonical`**, `effective_at`, `raw_json`, `source`, `source_request_id`, `meta_tag`, `version_id`, `sync_group_id`, `mapping_version`, `etag` |
| openEHR | `openehr_compositions` | compositions |

`cdr_app/app/models/resources.py:10` documents the common columns;
`cdr_app/app/models/__init__.py:96` is `HealthObservation`. The generic
`fhir_resources` table is explicitly **read-only legacy**
(`resources.py:17-20`).

**Terminology.** `code_canonical` is a **Path-B embedded guid** —
`urn:pdhc:concept/<guid>` — not a termbank URI, not LOINC, not SNOMED.
Consequences for cohort predicates:

- On the **per-type FHIR tables** the concept is *inside* `code_canonical`
  (`String(512)`, indexed at `resources.py:302`), so it is **not separately
  queryable**. `fhir_read.py:356-357` shows the existing workaround: exact
  match on the full canonical, else `LIKE '%/<code>'`.
- On **`health_observations`** and **`clinical_context`** there *is* a real
  indexed `concept_guid` column.

→ A cohort predicate on a concept has two different query shapes depending on
which store answers it. AN-1 and AN-4 must not assume one.

## 2. Patient identity

`patient_guid`, `String(36)`, indexed, on every observation-bearing table.
**No personnummer anywhere in the CDR observation tables.** The patient
registry lives in `ips.pdhc`.

→ Good news for the brief §4: the "if a CDR keys patients by personnummer, the
node maps to the GUID locally" branch **is not needed here**. The platform is
already GUID-keyed. `keyed_hash` linkage (AN-17) would need personnummer, which
the CDR does not hold — so that mode depends on ips, not on the CDR.

## 3. author_org, provider_org, timestamps, units, source

**This is the first place the brief and the repo disagree, and it matters.**

The brief states every observation is "tagged with `author_org` (the
organisation that created it) and `provider_org` (the organisation that
technically submitted it)". In the schema:

| Brief expects | Actually exists | Where |
|---|---|---|
| `author_org` | **does not exist under that name** | — |
| — | `requesting_org_guid` | `ClinicalContext`, `models/__init__.py:158` |
| `provider_org` | `provider_org_guid` ✓ | `ClinicalContext`, `:159` (indexed) |
| per-row org | `org_guid` (single) | per-type FHIR tables |
| — | *no org column at all* | `health_observations` — only `source_service` |

`ClinicalContext` (`models/__init__.py:137`) is the canonical **12-field**
clinical-context record (#302 widened it from 6), joined to the observation via
`ingest_raw_guid`: `patient_guid`, `service_request_guid`, `transaction_guid`,
`concept_guid`, `plan_definition_guid`, `care_plan_guid`, `contract_guid`,
`requesting_org_guid`, `provider_org_guid`, `requester_user_guid`,
`received_at`, `source_service`.

**Gap.** `requesting_org_guid` is *who ordered the data*, which is not the same
as *who authored it*. The brief's "each organisation remains responsible for
its own rows" and the node-policy question "may rows authored by other
organisations stored in this CDR be used" both rest on an `author_org` that
does not exist. **Proposal:** AN-0b should add a question — is
`requesting_org_guid` the intended author, or is a genuine `author_org`
missing from the model? Do not silently equate them.

**Timestamps.** `effective_at` (clinical time) and `received_at` (arrival) are
distinct on every store. The brief's index-event-relative day offsets must be
built from `effective_at`; `received_at` is an operational field.

**Units.** `unit` is a free `String(32)` on `health_observations`. Authority
for a concept's unit lives in `plan.pdhc`, not the CDR (see the `request.pdhc`
`context_service` work of 2026-09-23, #583).

## 4. Consent, objection, spärr — and whether a purpose can be declared

**What exists, and it is good.**

`ips.pdhc` owns the consent flags *and* the verdict. `POST
/api/v1/patients/analysis-filter` (`gateway/app/api/patient_routes.py:51`):

```
body    { patient_guids: [...], purpose: "...", research_project_guids: [...] }
returns { purpose, allowed: [...], excluded: [{guid, reason}, ...] }
```

- **It is already BULK** — it takes a list. The brief asks "does the read
  service support bulk reads"; for the *consent verdict*, yes.
- **Purpose is a closed enum** (`gateway/app/services/consent_policy.py:11`):
  primary use — `care`, `care_coordination`, `patient_access`,
  `administration`; secondary use — `research`, `statistics`,
  `quality_registry`.
- EHDS opt-out withdraws a patient from the **secondary** purposes only
  (`EHDS_SECONDARY_PURPOSES`, `:30`). `research` additionally requires
  per-project consent. `quality_registry` has its own opt-out.
- **Fails closed**: no ips verdict → 503, no data.

**Spärr** is separate: ips `/blocks/check`, zone model (inre / yttre /
nödöppning) in `care_access_policy.py`. analyse already uses `/blocks/check`.

**The gap, stated precisely.**

`cdr.pdhc/cdr_app/app/services/analysis_consent.py` (#422) enforces the
consent join **per reader**, deriving the purpose from the operator's *active
affiliation role* (researcher → research; quality/registry → quality_registry;
other clinical → statistics; SU-admin → administration). But `_operator_blob()`
at `:38-47` returns `None` — i.e. **passes through** — when:

```python
if blob.get("service_source"):   # service-key machine identity
    return None
```

The docstring is explicit that this is deliberate: *"a machine identity has no
role to derive a purpose from"*.

So **today a service-key read bypasses the consent gate at the CDR.**
`analyse.pdhc`'s `federation.py` reads exactly that way — `X-Source-Service:
analyse.pdhc`, `X-Service-Key`, `X-Org-Guids`, `X-Is-Admin` (`:175-180`).

analyse compensates in **application code**: `researcher.py:143`
`_apply_research_consent()` calls ips `analysis-filter` itself, fails closed on
503 — but with **`purpose` hard-coded to `"research"`**.

Three consequences for the epic:

1. Enforcement is real but at the **wrong layer** — in the analyse app, not at
   the read boundary. The brief requires it on the node.
2. The purpose is **fixed**, not a spec parameter. The brief requires an
   explicit, validated, logged purpose per analysis.
3. `clinical_read.py` — the one purpose-declaring read surface — is
   **care-delivery only** and hard-locked to the `dashboard.pdhc` service
   identity (403 otherwise, `clinical_read.py:63-70`). analyse **cannot** use
   it, by design. Correctly so: that is ADR-0001's split enforced in code.

**Proposal (do not work around — see ADR-0002).** The node must be able to
present a purpose as a machine and have it honoured. Raise a ticket against
`cdr.pdhc` to accept a declared secondary-use purpose from a registered
analysis service, rather than passing machine callers through. The purpose
values should be **ips's existing closed enum**, not a new vocabulary.

→ **The spec's `purpose` field must align with that enum.** The brief's example
uses `purpose: quality_followup`, which does not exist; the nearest real value
is `quality_registry`. AN-1 should adopt the platform enum.

## 5. The read log

`analyse.pdhc` has `AnalyseAudit` (`app/models/audit.py:41`): `timestamp`,
`user_guid`, `user_org_guids` (JSONB), `route`, `patient_guid`,
`n_rows_returned`, `response_status`, `session_id`, `event_type`,
`admin_justification`, `payload_snapshot` (JSONB). Applied by an `@audit_read`
decorator.

Close to what the brief wants, but **patient-centric**: one nullable
`patient_guid` per row. A group run has *no single patient*.

**Gap.** For AN-11 the audit row needs: `purpose`, `spec_hash`, `sources`,
per-source **suppressed** contributing-patient counts, and whether suppression
was applied. Extend the model; do not repurpose `patient_guid`.

## 6. Running CDRs with synthetic data

Deployed on `miserver`: `cdr.pdhc`, `cdr1`–`cdr5`, `cdr_6`, each under the
release-symlink layout (`current` / `releases` / `shared`).

`sim.pdhc` generates synthetic longitudinal data from real PlanDefinition terms
into `cdr_6` and can transfer it into an analysis CDR (`sim cdr-transfer`).
Synthea import exists for population-scale data.

**Not verified.** Standing two or three CDRs up *locally* side by side was not
tested, because **Colima is down on this laptop** after the macOS 27 upgrade
(2026-09-23). AN-10 must confirm it rather than inherit this claim.

## 7. Gap list

| # | Gap | Proposal |
|---|---|---|
| G1 | **A machine caller cannot declare a read purpose.** `analysis_consent._operator_blob` passes service-key callers through by design. | Ticket against `cdr.pdhc`: accept a declared secondary-use purpose from a registered analysis service. Use ips's existing enum. **Blocks AN-5.** |
| G2 | **Consent is enforced in the analyse app, not at the read boundary**, with `purpose` hard-coded to `research`. | Move to the node (AN-5); make purpose a spec parameter (AN-1). |
| G3 | **`author_org` does not exist.** Nearest is `requesting_org_guid` (who ordered ≠ who authored). | Operator question — added to AN-0b. Node policy "may rows authored by other orgs be used" cannot be built until this is settled. |
| G4 | **Concept is not separately queryable** on the per-type FHIR tables; it is embedded in `code_canonical`. | Two predicate shapes per store. Document in AN-1; do not assume one. |
| G5 | **The audit row is patient-centric**; a group run has no single patient. | Extend `AnalyseAudit` with purpose, spec_hash, sources, per-source suppressed counts (AN-11). |
| G6 | **The brief's `purpose` vocabulary does not match the platform's.** `quality_followup` is not a real value. | Adopt ips's closed enum in the spec (AN-1). |
| G7 | **No column projection / allowlist at the read boundary.** The node must project before computing. | Node-side projection (AN-2) until a read-service projection exists; propose the read-service extension. |
| G8 | **Bulk data read unverified.** The consent *verdict* is bulk; whether the data read supports the volumes a cohort needs was not tested. | Measure in AN-4 before choosing Polars vs DuckDB. |
| G9 | **`keyed_hash` linkage needs personnummer**, which the CDR does not hold. | That mode depends on ips, not the CDR. Record in AN-17's ADR. |

## 8. Reuse inventory — the restructure plan

Per [ADR-0003](decisions/0003-restructure-not-rewrite.md) this is the plan, not
a question.

| Module | Lines | Verdict |
|---|---|---|
| `app/analyse/federation.py` | 686 | **Refactor → coordinator.** Fan-out, per-CDR results, merge helpers, timeout handling already exist. Split: transport+merge to the coordinator, per-source computation to the node. |
| `app/routes/researcher.py` | 781 | **Split.** `_apply_research_consent` and `_record_export_audit` → node/coordinator respectively. Cohort + chart routes → the new spec-driven API. |
| `app/analyse/aggregations.py` | 331 | **Refactor → engine.** Candidate `local`/`merge` implementations; must be re-expressed in the three-function contract. |
| `app/analyse/cohort.py` | 126 | **Keep, adapt.** `CohortFilter`, predicate searches, `intersect_patient_sets` map onto the spec's `cohort.include`. |
| `app/analyse/stats.py` | 89 | **Refactor → engine.** `federated_stats()` is the seed of `describe`. |
| `app/analyse/observations_search.py` | 140 | **Keep → node read client.** |
| `app/analyse/canonical.py` | 98 | **Keep.** Deals with G4's two query shapes. |
| `app/analyse/openehr.py` | 121 | **Assess.** Out of the brief's scope; do not delete without checking rosetta's claim on it. |
| `app/analyse/patient_list.py` | 130 | **Remove** — #663. |
| `app/analyse/patient_detail.py` | 207 | **Remove** — #663. |
| `app/routes/clinical.py` | 153 | **Remove** — #663. |
| `models.Cohort`, `models.AnalyseAudit` | — | **Keep, extend** (G5). |

**Two inherited non-compliances** (already recorded in ADR-0003, repeated here
because they are easy to lose): `cohort_scatter` emits a raw scatter, which the
brief forbids in favour of a 2D binned heat map; `cohort_export` is a row-level
export path, which the brief requires off by default, `analyse:trusted` only,
single-node, pseudonymised and always logged.

## 9. #541 federation wiring — deployed-only

**Confirmed from the analyse side.** `/usr/local/www/analyse.pdhc/current/.env`
on `miserver` carries `CDR_ENDPOINTS`, `CDR_FANOUT_TIMEOUT`,
`ANALYSE_PDHC_SERVICE_KEY`, `IPS_BASE_URL` — configuration that is **not in
git**.

**Not fully confirmed:** the cdr-side registration of `analyse.pdhc` in each
CDR's known-services list. The deployed CDRs use the release-symlink layout and
my path probe did not reach the app directory. Someone should finish this check
before the restructure touches deployment config — losing it would silently
break federation.

---

## What this changes about the epic

1. **The consent story is mostly built.** ips's `analysis-filter` is bulk,
   purpose-aware, fail-closed and already used. The epic does not need to build
   a consent system; it needs to move enforcement to the node and make purpose
   a parameter.
2. **G1 blocks AN-5** and needs a ticket against `cdr.pdhc` — a cross-service
   dependency this epic did not previously know it had.
3. **G3 is an operator question**, not an engineering one, and it blocks part of
   the node policy model.
4. **The spec's purpose vocabulary must be the platform's**, not the brief's.
5. The platform being **GUID-keyed throughout** removes a whole branch of the
   brief's pseudonymisation section.

### Recommended next step

Answer AN-0b's remaining questions plus the new G3 question, and raise the G1
ticket against `cdr.pdhc`, **before** AN-1. Everything else in Phase 1 can
proceed.
