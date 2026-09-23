# ADR-0008 — no Polars or DuckDB yet; plain Python plus SciPy

Status: accepted · Date: 2026-09-23 · Ticket: #647 (AN-4)
Deferred to this ticket by [ADR-0004](0004-flask-not-fastapi.md).

## Context

The brief suggests "Polars or DuckDB for local computation". ADR-0004 adopted
the brief's data stack but deferred this particular choice to AN-4, "on
measured grounds".

All three candidates were checked for a Python 3.14 wheel, which ADR-0004
requires before committing to any new dependency: scipy 1.18.1, polars 1.44.2
and duckdb 1.5.5 all install cleanly. So this is not a availability decision.

## What a node actually computes

The three-function contract means a node returns **sufficient statistics**,
never a dataset: `n`, `n_missing`, `Σx`, `Σx²`, `Σxy`, cell counts, and a
bounded quantile sketch. That is a single linear pass over an already
projected and coarsened record stream. There are no joins, no grouped
aggregations over wide tables, no columnar scans — the shapes Polars and
DuckDB exist to make fast.

The cohort sizes in question are thousands to low millions of observations,
and the **read** through the platform read service dominates the local pass by
a wide margin.

## Decision

**Plain Python for the engine. SciPy for the reference distributions**
(t, chi-square, normal) that turn sufficient statistics into confidence
intervals and p-values. No Polars, no DuckDB.

## Alternatives considered

- **Adopt Polars now, as the brief suggests.** Rejected: it is a substantial
  dependency added on the strength of a guess about a bottleneck nobody has
  measured. Every dependency is carried by whoever operates the platform.
- **DuckDB, to push computation into SQL.** Rejected for the same reason,
  plus it would put a second query dialect in a codebase that already talks to
  Postgres through SQLAlchemy.

## Consequences

- `app/engine/` has no dataframe dependency. The partials are plain dicts,
  which also keeps them trivially JSON-serialisable for the node → coordinator
  hop — a property that would otherwise need a conversion layer.
- SciPy is used **only** for distribution functions, not for computation over
  data. That keeps it replaceable.

### What would change this decision

Measure before adopting either, and record the numbers:

1. A node's local pass, not its read, becomes the wall-clock bottleneck.
2. Gap **G8** is answered and the data read turns out to support cohort sizes
   where a Python loop is genuinely too slow.
3. An analysis type arrives that needs a real join or a grouped scan — the
   regression work in AN-16 is the first plausible candidate.

Until one of those is true with numbers attached, adding one would be
speculation dressed as engineering.
