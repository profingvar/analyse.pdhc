# Task: build analyse.pdhc, the group-level and federated analysis tool for PDHC

## 0. Scope and working rules

- Work only inside [[PATH TO PDHC REPO]]. Do not read, modify or reference any other folder in the workspace.
- The platform contains synthetic data only. Never create fixtures, seeds, logs or screenshots containing names, addresses or valid-looking personnummer. If identity values are needed in tests, use the repo's existing synthetic-identity generator or clearly fake values (e.g. prefixed `TEST-`).
- Follow the existing conventions of the repo (language, framework, linting, test runner, CI, container setup) over the suggestions in this prompt. Where you deviate from this prompt, say why.
- Work in the phases of section 11. At the end of each phase: stop, summarise what you built, list open questions and risks, and wait for my go-ahead.
- Record every non-trivial design choice as a short ADR in `docs/analyse/decisions/` (context, decision, alternatives, consequences).
- If something in the repo contradicts this prompt (schema, auth model, naming), trust the repo, flag the contradiction and propose an adjustment rather than silently working around it.

## 1. Context

PDHC (Planned Data in Healthcare) is a Vinnova-funded project demonstrating a vendor-neutral infrastructure for patient-generated and distributed care data. Observation data from sensors, forms and sampling are stored in a shared Clinical Data Repository (CDR). Key properties of the platform that analyse.pdhc must respect:

- Every observation is stored as its own row, tagged with `author_org` (the organisation that created it) and `provider_org` (the organisation that technically submitted it). Each organisation remains responsible for its own rows.
- Users authenticate through a central login service that issues a token carrying organisation, role and permissions.
- Reads never go directly to the store. A read service checks, per read, the organisation's relation to the patient, the purpose of the read, and that the patient has not objected or blocked access (spärr). Every read is written to a central read log (user, organisation, time, filter, number of rows).
- Consent register, block model, row-level filtering and central read log are built but not fully tested. Treat them as the intended interfaces.
- There may be several CDRs (e.g. one per region, as in the burn-care collaboration between Region Uppsala and Region Östergötland, with follow-up in a third home region). analyse.pdhc must work against one CDR or a federation of CDRs.

What analyse.pdhc is for: group-level description and comparison of cohorts, for quality follow-up, project evaluation and, later, research. It is not a clinical tool for individual patients.

Legal note to design around: the legal basis for secondary use of PDHC data in live operation is not settled. Therefore the purpose of every analysis must be an explicit, validated, logged parameter, and every node must have a policy hook that can refuse a purpose. Build for synthetic data now, and make it impossible to point the tool at live data without an explicit configuration change that is logged.

## 2. Phase 0: discovery (do this first, write no feature code)

Read the repo's documentation, schema, read-service API, auth integration, consent register, block model and read-log implementation. Write `docs/analyse/DISCOVERY.md` answering:

- How observations are modelled (openEHR compositions, FHIR Observations, a flat observation table, or other) and which terminology identifies an observation type (archetype ID, LOINC, SNOMED CT, local codes).
- How patient identity is stored: personnummer, platform GUID, both, and where the mapping lives.
- How `author_org`, `provider_org`, timestamps, units and data source (device, form, lab) are represented.
- How consent, objection and spärr are represented and checked, and whether the read service supports a purpose such as "analysis" and bulk reads.
- How the read log is written, and whether analysis runs can be logged through the same mechanism.
- How to start a local CDR with synthetic data, and how to start two or three CDRs side by side for federation tests.
- A gap list: what analyse.pdhc needs that does not exist yet, with a proposal for each.

Stop after Phase 0.

## 3. Architecture

```
 Browser: analyse UI  (token from central auth)
        |
        v
 analyse-coordinator   spec validation, policy, merge, disclosure control, audit
        |  signed analysis spec (no patient data)      ^  aggregates only
        v                                              |
 analyse-node @CDR1    analyse-node @CDR2    ...    analyse-node @CDRn
        |  reads via the platform read service (relation, purpose, consent, spärr)
        v
      CDR1                 CDR2                         CDRn
```

Principles:

- Code goes to the data, not data to the code. A node executes the analysis spec locally and returns aggregates (sufficient statistics), never patient rows, except in trusted mode (section 5).
- A single CDR is a federation with one node. There is one code path, not two.
- Nodes read through the existing read service and never bypass it. If the read service lacks what the node needs (purpose "analysis", bulk read, column projection), propose an extension to the read service rather than reading the store directly.
- Disclosure control runs twice: at the node before anything leaves it, and at the coordinator after merging.
- Everything is driven by a declarative, versioned analysis spec (section 6). The UI and the CLI both produce specs; neither sends free-form queries to nodes.
- Suggested stack unless the repo dictates otherwise: Python 3.12, FastAPI, Polars or DuckDB for local computation, SciPy and statsmodels for reference statistics, Pydantic for the spec, pytest with Hypothesis for property tests. Frontend in section 8.

## 4. Pseudonymisation and identity

- The analysis layer never receives names, personnummer, addresses, phone numbers, e-mail or free text. The node projects data with an allowlist of fields (never a blocklist) before any computation.
- The patient key inside analysis is the platform patient GUID. If a CDR keys patients by personnummer, the node maps to the GUID locally, and the mapping never leaves the node.
- Project pseudonyms: inside a node, replace the GUID with `pid = HMAC-SHA256(project_key, patient_guid)` truncated to a fixed length, with a separate `project_key` per analysis project. Pseudonyms from different projects cannot then be linked. Keys are held in the platform's secret store, never in code, specs or logs.
- Linkage across CDRs (the same patient treated by several organisations) is a configurable mode in the spec:
  - `shared_guid`: the platform issues the same GUID across CDRs, so patients can be deduplicated directly.
  - `keyed_hash`: each node computes `HMAC(linkage_key, personnummer)` locally, with the key held only by the nodes or a trusted third party. Used for deduplicated counts. Joining per-patient variables across nodes means patient-level data leaves a node, which requires trusted mode and a documented legal basis.
  - `none`: patients in different CDRs are treated as distinct, and the UI warns that the same patient may be counted more than once.
  - Implement `shared_guid` and `none` in the MVP. Put `keyed_hash` behind a feature flag with its own ADR.
- Quasi-identifiers are coarsened by default: dates become days relative to an index event (configurable per spec), calendar time only at month or year level, age in five-year bands (configurable), birth dates never exposed.
- The documentation and the UI must state that pseudonymised data are still personal data under GDPR, not anonymised data.
- Do not build any re-identification function.

## 5. Disclosure control

- Minimum cell size `k_min`, default [[5]], configurable per deployment but never lower than the configured floor.
- Counts below `k_min` are shown as `<k`. Apply secondary suppression so that suppressed cells cannot be recovered from row or column totals.
- Histograms: bins below `k_min` are merged with neighbours or suppressed. True min and max are never shown; show a configurable percentile range instead (default p5 to p95).
- Means and SDs only when n ≥ `k_min`; correlations only when n ≥ [[10]]; group comparisons only when every group meets the threshold.
- Differencing protection: the coordinator keeps a per-user history of cohort definitions and warns or blocks when two queries differ by fewer than `k_min` patients.
- Optional rounding (e.g. to the nearest 5) for exports marked as public.
- Row-level export is off by default. It is only allowed for role `analyse:trusted`, for a purpose the node policy permits, from a single node, with pseudonymised IDs only, and it is always logged.

## 6. The analysis spec

A spec is YAML or JSON, validated against a JSON Schema generated from the Pydantic models, canonicalised and hashed. The hash, the coordinator and node versions and the data snapshot times are attached to every result so any figure can be reproduced. Example (observation names are illustrative; use the ones found in Phase 0):

```yaml
spec_version: 1
title: "Home phase after burn injury, by injury size"
purpose: quality_followup          # validated against node policy, logged
sources: [cdr_uppsala, cdr_ostergotland]
linkage: none
index_event: { observation: injury_date }
cohort:
  include:
    - { observation: tbsa_percent, op: ">=", value: 5 }
    - { age_band: { from: 18 } }
variables:
  - { name: tbsa,        from: tbsa_percent,     agg: first }
  - { name: pain_w1_4,   from: pain_nrs,         agg: mean,  window_days: [0, 28] }
  - { name: n_reports,   from: patient_reported, agg: count }
  - { name: sex,         from: demographics.sex }
  - { name: site,        from: meta.author_org }
groups:
  - { name: "TBSA < 20%",  where: { tbsa: { lt: 20 } } }
  - { name: "TBSA >= 20%", where: { tbsa: { gte: 20 } } }
analyses:
  - { type: describe,       vars: [tbsa, pain_w1_4, n_reports, sex] }
  - { type: histogram,      var: pain_w1_4, bins: { width: 1, range: [0, 10] } }
  - { type: frequency,      vars: [sex, site] }
  - { type: correlation,    vars: [tbsa, pain_w1_4, n_reports], method: pearson }
  - { type: compare_groups, vars: [pain_w1_4, n_reports, sex] }
  - { type: over_time,      var: pain_nrs, bin_days: 7, range_days: [0, 90] }
```

Variable derivation (first, last, mean, min, max, count, slope, time to first event, within a window relative to the index event) happens on the node. Groups may overlap only if the spec says so; otherwise overlapping membership is an error.

## 7. Statistics engine

Every analysis type implements three pure functions: `local(data) -> Partial` on the node, `merge(list[Partial]) -> Partial` on the coordinator, and `finalize(Partial) -> Result`. Results are always given pooled and per source.

| Analysis | What the node returns | Federated result |
|---|---|---|
| Describe, continuous | n, missing, Σx, Σx², t-digest | Mean, SD, CI exact; median and IQR approximate |
| Describe, categorical | counts per level | Exact |
| Histogram | counts on bin edges fixed by the coordinator | Exact (data-driven bins need a first pass returning approximate percentiles) |
| Frequency table, cross-tab | cell counts | Exact; chi-square exact; Fisher single-node only |
| Pearson correlation, matrix | n, Σx, Σy, Σx², Σy², Σxy per pair (pairwise complete) | Exact |
| Spearman correlation | ranks need all data | Exact on one node; approximate across nodes, flagged |
| Compare groups, continuous | the describe partial per group | Welch t-test, mean difference with CI, standardised mean difference exact; Mann-Whitney single-node only |
| Compare groups, categorical | counts per group and level | Chi-square, risk difference, SMD exact |
| Over time | n, Σx, Σx² per time bin since index | Mean with CI per bin exact |
| Data completeness | per-patient observation counts as histogram; missingness per variable; rows by provider_org and source | Exact |
| Linear regression (later) | XᵀX, Xᵀy, yᵀy, n | Exact |
| Logistic regression (later) | gradient and Hessian per iteration | Exact via federated Newton-Raphson |
| Kaplan-Meier (later) | events and at-risk per interval | Exact |

Rules:

- Missing data are always reported explicitly, never silently dropped.
- 95 % confidence intervals by default. Effect sizes are shown before p-values.
- When several groups or variables are compared, show unadjusted p-values with a clear note, and offer Holm adjustment.
- Results carry a flag `exact | approximate` and the UI shows it.
- Results never say "effect" or "causes"; comparisons are descriptive.

Testing requirements:

- Property tests: for random synthetic data split at random across 1 to 5 nodes, every exact method must equal the pooled SciPy or statsmodels result within 1e-9 relative tolerance. Approximate methods must stay within a documented error bound.
- Disclosure tests: no result, log line, error message or export contains a cell below `k_min`, a GUID, a personnummer pattern or a name from the fixtures. Add a scanner that greps all outputs of the test suite for identifier patterns and fails the build on a hit.
- Policy tests: a node refuses a purpose that is not in its policy, excludes rows under spärr, and excludes patients who have objected to secondary use.

## 8. Frontend for non-experts

Design goal: a clinician or project coordinator without statistical training should get from a question to a correct, readable answer in under two minutes, without choosing a statistical test.

Suggested stack unless the repo dictates otherwise: React with TypeScript, Vega-Lite for charts, and the repo's existing component library. Swedish as default UI language with English available. WCAG 2.1 AA. A colour-blind-safe palette (Okabe-Ito) used consistently, so that a subcohort has the same colour in every chart.

Layout:

```
+--------------------------------------------------------------------------+
| analyse.pdhc   Sources: Uppsala ✓ Östergötland ✓  Pseudonymised, <5 hidden|
+--------------------------------------------------------------------------+
| 1 Data  >  2 Cohort  >  3 Question  >  4 Result                          |
+-------------------+--------------------------------------+---------------+
| COHORT            |  RESULT CANVAS                       | SETTINGS      |
| Patients who...   |  [chart]                             | Bins          |
|  + TBSA >= 5 %    |                                      | Time window   |
|  + age 18+        |  "In the group TBSA >= 20 % mean     | Show per site |
|  n = 214          |   pain weeks 1-4 was 1.3 points      | Expert mode   |
|                   |   higher (95 % CI 0.6-2.0)..."       |               |
| GROUPS            |  [table]   [How to read this ▾]      |               |
|  ● TBSA < 20 %    |                                      |               |
|  ● TBSA >= 20 %   |  [Save recipe] [Export report]       |               |
|  [+ Compare]      |                                      |               |
+-------------------+--------------------------------------+---------------+
```

The four steps:

- Data: tick which CDRs to include. Each source shows status (online, data snapshot time, number of eligible patients as a suppressed count) and the linkage mode, with a plain warning when patients may be counted twice.
- Cohort: a sentence-style criteria builder ("Patients who have [observation] [at least] [value] [within] [days of index event]"), with searchable observation names in plain language and a live, suppressed patient count that updates as criteria change.
- Question: cards phrased as questions, not methods. The tool picks the method from the variable types:

| Question card | Variable types | Method and chart chosen automatically |
|---|---|---|
| Describe my cohort | any | Table 1 style summary |
| How is X distributed? | continuous | Histogram with median and IQR marked |
| How common is Y? | categorical | Frequency table with bar chart |
| Are X and Y related? | two continuous | Pearson or Spearman, 2D binned heat map (never a raw scatter) |
| Are X and Y related? | two categorical | Cross-tab with chi-square, stacked bars |
| How do groups differ? | groups plus any variables | Side-by-side table, overlaid histograms, differences with CI |
| How does X change over time? | repeated observations | Mean curve per group with CI band |
| How complete are the data? | any | Missingness, reports per patient, rows per source |

- Result: an automatically chosen chart, one plain-language summary sentence generated from a template (never free-form text), a compact table, an expandable "How to read this" panel, a visible `exact` or `approximate` badge, and suppressed cells shown as `<5` with an explanatory tooltip.

Comparing subcohorts:

- A persistent "Compare" button available from every result. Groups are built with the same sentence builder as the cohort, up to [[4]] groups, each with a fixed colour and an editable name.
- Every chart and table re-renders for all groups at once. The default comparison view shows a side-by-side Table 1 with standardised mean differences, which is easier for non-experts to read than p-values.
- The UI warns when groups overlap, when a group is below the threshold, and when a comparison is based on approximate methods.
- "Per site" toggle to see whether a pooled difference holds in each source.

Other requirements:

- Save any analysis as a named recipe that others with the right role can rerun on fresh data. Share by link that contains only a recipe ID, never parameters that identify people.
- Export a report (Word or PDF, plus CSV of aggregates) with charts, tables, summary sentences, the spec hash, sources, snapshot times and the privacy settings used.
- Expert mode shows the YAML spec, the method details and test statistics, and lets experts edit the spec directly.
- Undo, clear empty states, helpful errors in plain language ("This group has too few patients to show safely. Try widening the criteria.").
- No chart type that needs expert interpretation by default.

## 9. Command line interface

```
analyse-pdhc spec validate spec.yaml
analyse-pdhc run spec.yaml [--sources cdr1,cdr2] [--out results/]
analyse-pdhc run spec.yaml --dry-run         # counts per source only, suppressed
analyse-pdhc node serve --config node.yaml   # runs a node beside a CDR
analyse-pdhc coordinator serve --config coordinator.yaml
analyse-pdhc sources list                    # status of configured nodes
analyse-pdhc report results/ --format docx|pdf|csv
analyse-pdhc synth --nodes 3 --patients 2000 # synthetic multi-CDR test setup
```

The CLI uses the same coordinator API as the UI, authenticates with the central login service, and never prints identifiers.

## 10. Security, policy and audit

- Roles: `analyse:viewer` (runs saved recipes), `analyse:analyst` (builds specs), `analyse:trusted` (row-level export, section 5), `analyse:admin` (configuration). Map them to the platform's existing role model.
- Each node has a policy file owned by the organisation behind the CDR: permitted purposes, permitted analysis types, `k_min` floor, whether its rows may be pooled with other sources, and whether rows authored by other organisations stored in this CDR may be used. The node enforces its own policy; the coordinator cannot override it.
- Rows under spärr and patients who have objected to secondary use are excluded on the node before any computation.
- Every run is written to the platform read log: user, organisation, purpose, spec hash, sources, number of contributing patients per source (suppressed), and whether suppression was applied. Each node also logs locally, so every author organisation can see which analyses used its data.
- mTLS between coordinator and nodes. Nodes accept only specs signed by the coordinator. No identifiers in URLs, logs, metrics or error messages.
- A configuration switch `data_mode: synthetic | live`, defaulting to `synthetic`. Switching to `live` requires an admin action, is logged, and shows a persistent banner in the UI.

## 11. Phases and acceptance criteria

- Phase 0, discovery: `DISCOVERY.md` and gap list as in section 2. Stop.
- Phase 1, single-node engine and CLI: spec models and schema, node with allowlist projection and pseudonymisation, describe, histogram, frequency, correlation, disclosure control, CLI `validate`, `run` and `--dry-run`, full test suite including the identifier scanner. Accepted when all tests pass against one synthetic CDR. Stop.
- Phase 2, federation: coordinator, `synth` command producing two to three CDRs in the repo's container setup, merge functions, per-source results, compare groups, over time, data completeness, audit logging, node policies. Accepted when the property tests prove federated equals pooled for all exact methods. Stop.
- Phase 3, frontend MVP: the four-step flow, question cards, compare groups, recipes, report export, Swedish and English. Accepted after a usability walkthrough script in `docs/analyse/usability-test.md` with five tasks a non-expert should complete unaided. Stop.
- Phase 4, extensions: regression, Kaplan-Meier, `keyed_hash` linkage behind a flag, differencing protection hardening.

## 12. Deliverables and structure

Propose a structure consistent with the repo, roughly:

```
analyse/
  spec/          Pydantic models, JSON Schema, canonicalisation and hashing
  engine/        one module per analysis type: local, merge, finalize
  privacy/       projection allowlist, pseudonymisation, disclosure control
  node/          node service, policy enforcement, read-service client
  coordinator/   API, spec signing, merge orchestration, audit
  cli/
  ui/
  tests/         unit, property, disclosure, policy, end-to-end
docs/analyse/    DISCOVERY.md, decisions/, user guide (sv, en), method notes
```

At each stop, give me: what was built, how to run it, test results, open questions, and the decisions you need from me.
