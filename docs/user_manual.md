# analyse.pdhc — user manual

analyse.pdhc is the **population / research** workspace of the PDHC platform.
It lets an analyst define a **cohort** of patients across the regional CDRs and
look at aggregate, de-identified distributions — never a single patient's chart
(that is the clinical dashboard / cd-assist).

Access requires the **analysis phase** on your account (or SU admin). A
treating clinician without the analysis phase will be turned away here — use
the clinical dashboard instead.

## Logging in
1. Open `https://analyse.pdhc.se/`.
2. You are redirected to the single-sign-on page — sign in as usual.
3. If your account has the analysis phase you land on the **Researcher
   workspace**. If not, you are returned to the login page with a notice.

Your session is re-checked with SSO on every action, so if you log out (or an
admin revokes your session) access stops immediately.

## Defining a cohort
1. In **Define a cohort**, edit the filter (JSON). Common fields:
   - `cdr_ids`: which CDRs to search (empty = all configured).
   - `demographics`: `age_min`, `age_max`, `sex`.
   - `conditions`: list of condition code URIs (e.g. a SNOMED diabetes code).
   - `medications`: list of medication code URIs (e.g. an ATC class).
2. Click **Define cohort**. The cohort is resolved across the CDRs, passed
   through the consent filter, and saved. You see the cohort id and the member
   count (`n`).
3. The **Recent cohorts** table lists your saved cohorts.

Cohort membership is the intersection ("AND") of every predicate you supply.
Members are always consent-filtered: patients who have opted out of secondary
use, or who have not consented to your research project, are excluded — and the
exclusion is re-applied on every later read, so a consent withdrawn after you
defined the cohort takes effect immediately.

## Aggregate views (per cohort, via the JSON API)
For a saved `<cohort_id>`:
- **Histogram** — distribution of a variable:
  `GET /api/cohort/<id>/variable/<canonical>/histogram`
- **Boxplot** — stratified summary (`?group_by=region|cdr`):
  `GET /api/cohort/<id>/variable/<canonical>/boxplot`
- **Scatter** — paired latest values of two variables (`?x=&y=&max=`):
  `GET /api/cohort/<id>/scatter`
- **Trend** — per-month percentiles over a window (`?canonical=&window=`):
  `GET /api/cohort/<id>/trend`
- **Export** — streamed CSV (`?format=csv&variables=<a,b,...>`):
  `GET /api/cohort/<id>/export`

A `<canonical>` is a variable identifier in either `system|code` or
`.../system/code` form.

## Privacy & audit
Every read (including denied ones) is recorded in the analyse kontroller log
with who, when, which route, how many rows, and the purpose. Exports write an
additional receipt row with the export id and the exact streamed row count.
These logs exist to satisfy the Patientdatalag chain-of-custody requirements —
treat cohort data as sensitive and export only what your project needs.

## Getting help
If a cohort resolves to `503`, the consent service (ips.pdhc) was temporarily
unreachable — research reads fail closed by design; retry shortly. For access
problems (analysis phase, research project membership), contact your PDHC
operator.
