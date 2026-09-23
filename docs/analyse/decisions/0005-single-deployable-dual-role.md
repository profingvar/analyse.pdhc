# ADR-0005 — one deployable, running either role by configuration

Status: accepted · Date: 2026-09-23 · Decided by: operator · Ticket: #643 q4

## Context

The architecture has two services — a coordinator and N nodes. They share the
spec models, the privacy layer, the disclosure-control rules and the three-part
engine contract (`local` / `merge` / `finalize`). Only the entry points differ:
a node runs `local`, a coordinator runs `merge` and `finalize`.

analyse.pdhc owns ports **9110–9119**; 9110 (app) and 9111 (db) are in use,
9112–9119 are free, and onboard.pdhc reserves 9120 upward.

## Decision

**One deployable**, whose role is chosen by configuration:

```
analyse-pdhc coordinator serve --config coordinator.yaml
analyse-pdhc node serve --config node.yaml
```

Port allocation inside the existing block:

| Port | Role |
|---|---|
| 9110 | coordinator (the user-facing role, keeps the current app port) |
| 9111 | database |
| 9112–9118 | node instances, one per CDR |
| 9119 | spare |

## Alternatives considered

- **Two separate services, two repos, a new port block.** Rejected: they share
  most of their code, and splitting them would either duplicate the engine or
  require a shared library published between two repos, for no gain.
- **One process serving every CDR.** Rejected — see below.

## Consequences

- One image, one test suite, one release. Role-specific code sits behind the
  entry point, never behind scattered conditionals.
- The single-CDR case is genuinely the federated case with one node, as the
  brief requires. There is no second code path to keep honest.

### Still open: how many node instances

"One deployable" settles packaging, not topology. The recommendation, to be
confirmed before AN-5:

**One node instance per CDR**, not one node multiplexed across CDRs.

The reason is the trust model rather than performance. Each node's policy file
is *owned by the organisation behind that CDR* — permitted purposes, permitted
analysis types, the `k_min` floor, whether its rows may be pooled, whether rows
authored by other organisations may be used — and the coordinator cannot
override it. Each node also keeps a local audit log so every author
organisation can see which analyses touched its data. A single process holding
every organisation's policy and every organisation's audit log makes that
ownership fictional: one operator, one blast radius, one place to get it wrong.

Today every CDR is a container on one Docker VM on miserver, so "beside the
CDR" is notional. That is a fact about the current deployment, not a licence to
assume co-location: the code must work when Uppsala and Östergötland are
genuinely separate hosts, because that is the case the federation exists for.
