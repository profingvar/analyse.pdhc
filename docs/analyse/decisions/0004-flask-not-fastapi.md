# ADR-0004 — Flask, not FastAPI

Status: accepted · Date: 2026-09-23 · Decided by: operator · Ticket: #643 q3

## Context

The reconstruction brief suggests "Python 3.12, FastAPI, Polars or DuckDB,
SciPy and statsmodels, Pydantic, pytest with Hypothesis" — while also saying
that the repo's existing conventions win over its own suggestions.

The repo is Python 3.14 with Flask, Flask-SQLAlchemy and Flask-Migrate.
CLAUDE.md §6 mandates Flask behind gunicorn for every service on the platform,
with a specific start.sh contract, pid-file handling and port-block discipline.

## Decision

**Flask**, on the platform's existing Python 3.14 and gunicorn pattern.

The brief's *data* stack is adopted: Pydantic for the spec models, SciPy and
statsmodels as the statistical reference, Hypothesis for property tests, and
Polars or DuckDB for local computation (choice deferred to AN-4 on measured
grounds, recorded then).

## Alternatives considered

- **FastAPI as the brief suggests.** Rejected: it would make analyse the only
  service on the platform not following §6, with its own process model,
  start-up contract and deployment shape. The operational cost of that
  divergence is paid forever by whoever runs the box, and buys nothing the
  analysis layer needs.
- **FastAPI for the node, Flask for the coordinator.** Rejected outright: two
  frameworks for one deployable (ADR-0005) is the worst of both.

## Consequences

- Pydantic is used for spec modelling and JSON Schema generation only, not as a
  request-validation framework wired into routing.
- New dependencies land in this repo for the first time: pydantic, scipy,
  statsmodels, hypothesis, and polars or duckdb. Note that `psycopg2-binary`
  has no Python 3.14 wheel on this platform; check wheel availability for each
  new dependency on 3.14 before committing to it, and record any that force a
  container-only path.
- The brief's async assumptions do not carry over. Fan-out from coordinator to
  nodes is concurrent I/O in a Flask app — threads or a worker — not an async
  framework. AN-8 records how.
