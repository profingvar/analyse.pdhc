# ADR-0003 — restructure the existing analysis code, do not rewrite it

Status: accepted · Date: 2026-09-23 · Decided by: operator · Ticket: #642 (AN-0)

## Context

The brief is framed as a "total reconstruction" and proposes a greenfield tree
(`analyse/spec`, `engine`, `privacy`, `node`, `coordinator`, `cli`, `ui`).

The repository is not greenfield. `analyse_app/app/analyse/` holds ~1,939 lines
of working analysis code, including:

| Module | Lines | What it is |
|---|---|---|
| `federation.py` | 686 | federation across CDRs, working today |
| `researcher.py` (routes) | 781 | cohort definition, histogram, boxplot, scatter, trend, export with audit, research-consent filtering |
| `aggregations.py` | 331 | aggregation primitives |
| `cohort.py` | 126 | `CohortFilter`, predicate searches, set intersection |
| `stats.py`, `observations_search.py`, `canonical.py`, `openehr.py` | ~350 | supporting |

## Decision

**Restructure, do not rewrite.** Existing modules are refactored into the
node/coordinator split described in the brief. Nothing is discarded without a
specific reason recorded here.

## Alternatives considered

- **Rewrite from zero.** Rejected: discards a working federation layer and a
  cohort workspace, and would re-earn bugs already fixed — notably the
  fail-open spärr defect closed via `/blocks/check`.
- **Keep the old tree and build the new one beside it.** Rejected: two analysis
  paths is precisely what the brief forbids ("one code path, not two"), and the
  old path would rot.

## Consequences

- AN-0 (#642) item 8 changes character: the reuse inventory is no longer a
  question of *whether* to reuse, it is **the restructure plan**. Each module
  gets a verdict — keep as-is, refactor into which new home, or replace with a
  reason.
- The brief's proposed tree is a target shape, not a mandate. Where the existing
  layout is sound, it wins; deviations are recorded.
- **Inherited code is not automatically compliant.** Two known gaps in the code
  being kept:
  - `cohort_scatter` emits a raw scatter; the brief requires a 2D binned heat
    map and forbids raw scatters.
  - `cohort_export` is a row-level export path; the brief requires row-level
    export off by default, `analyse:trusted` only, single-node, pseudonymised
    and always logged.
  Both must be reconciled in Phase 1. Surviving the restructure is not evidence
  of compliance.
- #541's federation wiring is recorded as **deployed-only, not in git**. The
  restructure must not silently drop it; AN-0 verifies it on the server first.
