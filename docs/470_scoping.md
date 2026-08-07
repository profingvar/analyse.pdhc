# Split the dashboard analyse engine — **rename dashboard → cd-assist** + extract **analyse** (#470)

**Status:** SCOPING (pre-build) · **PIVOT 2026-08-07:** cd-assist is NOT a greenfield service — it IS the existing individual clinical dashboard (dashboard.pdhc) renamed/refocused. **analyse.pdhc is the one genuinely-new service.** Supersedes the earlier "two greenfield services" framing (and #470's original single-service framing).
**Parents:** #462, #463 (D1), #287–293 (cdr1/analyse split) · **Coordinates with:** #293 (CDR read-lockdown), gateway.pdhc

---

## 0. TL;DR (revised)

The dashboard has two halves: an **individual / point-of-care** side (single-patient `/charts` care-delivery view + the nurse AGP/variable/events views) and a **group / population** side (researcher cohorts + federated system reads). The pivot:

- **cd-assist = dashboard.pdhc, renamed/refocused.** It already has the SSO web login flow, `/charts` (CDR1-backed single-patient charting, care-delivery, spärr incl. #471.4), the care-delivery gate (#463), and audit. We **fold the nurse single-patient views into it** (re-gated analysis-phase → care-delivery + care-unit scope + spärr), retire the analysis-phase nurse routes from the analyse bundle, and rename the service. **Little new code** — mostly a fold + a rename.
- **analyse.pdhc = the ONE new greenfield service.** It carries the group/population + **federated-events** layer: researcher/cohort engine + the gateway/monitor-facing federated endpoints (`/api/v1/observations`,`/stats`,`/canonical`,`/openehr`).

**Disposition of the earlier greenfield `cd-assist.pdhc`** (built as commits `edf1d5c`/`a0b4382` + an uncommitted SSO flow): **discarded** — it duplicates what dashboard already has. Its one valuable output — the **#536 nurse-engine transformation** (nurse.py → care-delivery gate + care-unit scope + spärr) — is **salvaged as a patch into dashboard**, then the greenfield dir is removed.

---

## 1. cd-assist = dashboard.pdhc, renamed (the individual side)

**Keep as-is (already in dashboard):** `/charts` single-patient view, SSO web login flow, `has_care_delivery_access` gate (#463), `dashboard_audit`, `cdr1_client`, `views/picker/designs`.

**Fold in (from the analyse bundle, re-gated):** the nurse single-patient views `nurse.py` → `/api/nurse/patient/<g>/{summary,agp,variable,events}`, **re-gated analysis-phase → care-delivery** (`@require_care_delivery` / `sso_login_required`), care-unit patient-scoped, spärr per-patient (coarse + #471.4 lift). The transformation is already worked out in the discarded greenfield `cd_assist_app/app/api/nurse.py` — port that logic into dashboard's care-delivery blueprint (it uses dashboard's own `ips_client`/`audit`/`federation` — federation/aggregations may need adding to dashboard if not present).

**Rename:** dashboard.pdhc → cd-assist.pdhc (repo name, service identity, docs, www.pdhc.se card). **Host: OPERATOR decision** — new `cd-assist.pdhc.se` vhost, or keep dashboard's host and rename internally. (#463 already had the #462 clinical dashboard taking over dashboard.pdhc's slot, so "keep host, rename" is the low-friction path.)

**Retire from the analyse bundle:** the analysis-phase nurse routes (they now live in cd-assist, care-delivery-gated).

## 2. analyse.pdhc = the one new greenfield service (group + federated events)

Unchanged from the group-side plan: scaffold containerised (from `cdr_6.pdhc`), analysis-phase gate, own DB with `cohort` + `analyse_audit`. Port `researcher.py` + `cohort` + the 4 gateway/monitor federated endpoints (`observations_search`/`canonical`/`stats`/`openehr`) + the read core (`federation.py`+`aggregations.py` copy). Repoint gateway's `ANALYSE_BASE_URL` → analyse.pdhc (the hard dependency). CDR2–6 read-identity flip for analyse (#293). Cutover + delete the group half from dashboard(cd-assist).

## 3. Read core (federation + aggregations)
Both sides need it. In the pivot, cd-assist (=dashboard) gets it via the nurse fold; analyse gets its own copy. If dashboard doesn't already carry `federation.py`/`aggregations.py` (they were in the analyse bundle), copy them into the cd-assist(dashboard) care-delivery path for the nurse views. D5 drift note applies (dashboard/cd-assist + analyse).

---

## 4. Revised ticket map
- **Rollup #533** (updated via a pivot note): cd-assist = rename dashboard; analyse = the new service.
- **Superseded** (greenfield cd-assist, work discarded except the salvaged nurse patch): #535 scaffold, #536 nurse-port (salvaged), #537 cutover-docs (the docs/card are reusable for the renamed dashboard).
- **New cd-assist tickets:** (a) fold nurse into dashboard (re-gate care-delivery + spärr; salvage from greenfield #536); (b) rename/refocus dashboard.pdhc → cd-assist (host/identity/docs/cards; retire analysis-phase nurse); (c) discard the greenfield cd-assist.pdhc after (a) salvages from it.
- **analyse tickets unchanged:** #538 scaffold, #539 port, #540 gateway repoint, #541 CDR identity flip, #542 cutover, #543 delete group half from dashboard(cd-assist), #544 cleanup.

## 5. Risks / decisions
- **Host for cd-assist** (operator) — new vhost vs keep dashboard's. Low-friction = keep + rename.
- **Nurse fold** must land in dashboard's *care-delivery* blueprint (not analysis-phase) — salvage the greenfield transformation, don't re-derive.
- **Gateway `ANALYSE_BASE_URL`** repoint still the hard dependency (analyse side, #540).
- **Discard greenfield only AFTER** the nurse patch is salvaged into dashboard.
- The federation/aggregations read core: confirm whether dashboard already has it or it must be copied for the nurse views.

## 6. Non-goals
No analyse logic rewrite; the #287–293 split is done; the care-delivery /charts view is untouched except adding the nurse views alongside it.
