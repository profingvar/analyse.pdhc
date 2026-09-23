# ADR-0007 — the spärr-log viewer goes, the audit records stay

Status: accepted · Date: 2026-09-23 · Ticket: #663 (AN-R)

## Context

[ADR-0001](0001-group-analysis-only.md) removes individual-patient analysis
from analyse.pdhc. One of the removed surfaces is the **admin spärr log**
(`/admin/sparr-log`), built under #579 as oversight over spärr exposures and
break-glass reads.

That view has two parts that are easy to conflate: the **viewer** (a route and
a template) and the **records** (rows in `analyse_audit`). They have different
obligations.

## Decision

- The **viewer is removed** — route, template and the `@admin_required` page.
- The **records are retained**. `AnalyseAudit`, the `analyse_audit` table and
  its migration are untouched, and `@audit_read` continues to write rows on the
  group routes.

## Reasoning

The viewer can go because after this change analyse.pdhc no longer exposes
individual patient data, so it generates no new exposures to oversee. The
equivalent oversight for individual reads lives in dashboard.pdhc
(`admin.audit_view` over `DashboardAudit`), which is where those reads now
happen.

The records cannot go, for three reasons:

1. **Read logging is a PDL obligation.** The rows document who read what, when,
   under which session. Deleting them because the tool that displayed them was
   retired would destroy evidence about reads that actually occurred.
2. **Removing a route is reversible; deleting an audit trail is not.** If this
   turns out to have been the wrong call, a route can be rebuilt from git. The
   rows cannot be reconstructed from anywhere.
3. **The table is still in use.** `@audit_read` remains on the group routes, so
   `analyse_audit` is a live table, not a historical one. It is extended in
   AN-11 (#654) with `purpose`, `spec_hash`, `sources` and per-source
   suppressed counts — not replaced.

## Alternatives considered

- **Drop the table with the route.** Rejected — see above. This is the failure
  mode the decision exists to prevent.
- **Migrate the historical rows to dashboard.pdhc.** Rejected for now. It is
  defensible in principle (the reads they describe are the kind dashboard now
  serves), but it moves records across a service boundary and across two
  different audit schemas, which risks losing or mis-stamping them. Leaving
  them where they were written keeps their provenance exact. Revisit only if
  someone needs a single place to query individual-read history.

## Consequences

- `analyse_audit` retains rows written before 2026-09-23 that carry a
  `patient_guid`. After this change no new row will. A reader of the table must
  not assume `patient_guid` is always null, nor always present.
- AN-11 extends this model for group runs. It must **not** repurpose
  `patient_guid` for anything else — that column's historical meaning is
  load-bearing.
- If the records are ever moved or pruned, that is its own decision with its own
  ADR and a retention rationale. It is not a tidy-up.
