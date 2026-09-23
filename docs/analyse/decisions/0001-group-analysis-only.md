# ADR-0001 — analyse.pdhc is group analysis only

Status: accepted · Date: 2026-09-23 · Decided by: operator · Ticket: #663 (AN-R)

## Context

analyse.pdhc today serves both an individual-patient view (org-scoped patient
list, per-patient dashboard, per-clinic spärr enforcement, admin spärr log —
#578/#579, deployed 2026-09-03 as 0.2.0-reform) and a group/cohort workspace
(`researcher.py`, `federation.py`, `aggregations.py`).

The reconstruction brief states plainly that analyse.pdhc "is not a clinical
tool for individual patients". That contradicts a capability deployed three
weeks earlier, so it needed an explicit decision rather than a silent
interpretation.

## Decision

There is a hard separation of concerns:

- **dashboard.pdhc** — individual analysis.
- **analyse.pdhc** — group analysis.

All individual-patient analysis is removed from analyse.pdhc.

## Alternatives considered

- **Keep both in one service.** Rejected: the two have different legal bases
  (care delivery vs secondary use), different disclosure rules, and different
  audiences. Housing them together is what made the spärr and consent model
  hard to reason about in the first place.
- **Delete without a home for the individual view.** Rejected: #579's
  capability would simply be lost.
- **Move the individual view into analyse as a sub-mode.** Rejected: it is the
  same coupling under a different name.

This decision is consistent with the existing #462 split (single-patient
clinical dashboard on CDR1 supplants the old dashboard; the analyse engine moves
to analyse.pdhc). It completes that separation rather than opening a new one.

## Consequences

- `clinical.py`, the `views.py` patient chooser and patient page,
  `patient_list.py`, `patient_detail.py` and their tests are removed (#663).
- The landing page becomes the group workspace.
- **Audit records are retained.** The spärr-log viewer goes, because analyse
  will no longer expose individual data and so generates nothing new to view.
  The stored rows are a PDL read-logging obligation and are kept (or migrated),
  never dropped with the route. See #663.
- dashboard.pdhc must be confirmed to cover what is removed, or the gap is
  raised there. Removal without that check is capability lost, not moved.
- #579's outstanding operator live-smoke covers surfaces being deleted and
  should not be run.
