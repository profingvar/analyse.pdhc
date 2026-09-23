# ADR-0011 — the coordinator↔node wire is sealed over transmitted bytes

Status: accepted · Date: 2026-09-23 · Ticket: #684 (AN-12)

## Context

Phases 1–4 built a node that computes partials and a coordinator that merges
them, and nothing that carried a partial between two processes. Every test
drove both halves in one process, so "federated" was true of the mathematics
and not of the deployment. ADR-0005 settled that one deployable runs either
role; this settles what travels between them.

Two things had to be decided that the in-process path never had to answer:
what authenticates a request, and what a node needs that the coordinator must
not supply.

## Decision

### 1. The MAC covers the transmitted octets, not the parsed object

An envelope is serialised once, HMAC'd over those exact bytes, and verified
over the bytes as received **before anything parses them**.

The obvious alternative — parse, re-serialise canonically, compare — makes the
signature a property of what the receiver's parser produced rather than of
what the sender sent. Any parser disagreement (duplicate keys, float
formatting, unicode normalisation) becomes a gap between what was signed and
what is acted on.

`coordinator/signing.py` may safely canonicalise, because a spec is a model
with exactly one canonical form and AN-1 exists to guarantee that. Partials are
not: they carry floats and sketch centroids, which is precisely where
re-serialisation moves bytes.

The MAC binds `kind` and `issued_at` as well as the body, so a response cannot
be replayed into a request endpoint and a captured envelope cannot be re-dated.

### 2. Two signatures, answering two different questions

- The **envelope** signature says *the other half of this deployment sent
  these bytes, unaltered*.
- The **spec** signature says *the coordinator approved this analysis*, over
  the spec's canonical form.

The spec signature is **required**, never optional. A node that ran unsigned
specs whenever the field was absent would be a node an attacker could use by
omitting it. The signing secret defaults to the transport secret, because the
normal deployment has one secret store; setting it separately is what allows
approval to be a different authority from transport.

### 3. No patient identifier crosses the boundary, in either direction

The coordinator sends a question. Each node decides which of *its* patients the
question is about, through a `CohortSource` it owns. A coordinator that sent
patient guids would be holding identifiers for every source — the arrangement
the whole design exists to avoid — and the AN-7 scanner would flag them in its
own output.

The project key is likewise loaded by each node from its own environment and
never travels.

### 4. A source that did not answer is named as one

Every failure path ends in `combine()`'s `failures` map, which produces a
degraded `SourceStatus` and a note on the result. A merged figure over four of
five sources is a different number from the same figure over five, and the two
are indistinguishable to a reader unless the result says which it is.

The one case that is **not** a degraded result: when *no* node answered,
`run_distributed` raises. `combine` would otherwise return a result with zero
sources that renders as a page of suppressed cells — visually identical to a
real analysis of a very small population, and an analyst would reasonably read
it as one.

### 5. The node surface exists only where the deployment is a node

`ANALYSE_ROLE` gates blueprint registration. A coordinator that also served
`/api/v1/node/run` would be a second, quieter route to the data for anyone
holding the transport secret.

## Deviation from ADR-0005

ADR-0005 sketched the role selection as CLI subcommands
(`analyse-pdhc node serve --config node.yaml`). This is implemented as
`ANALYSE_ROLE` in the environment instead, because the service runs as a Flask
app under gunicorn like every other PDHC service, and the platform's start.sh
contract has no place for a subcommand. The decision ADR-0005 actually made —
one deployable, role from configuration — is unchanged.

## What this does NOT settle

**Cohort resolution.** A node still cannot derive a cohort from
`spec.cohort.include`. Two separate things are missing, neither of them
transport: `run_spec()` never reads the spec's inclusion criteria at all, and
the CDR exposes no criterion-search or patient-listing endpoint a node could
use. `ConfiguredCohortSource` — the operator pins the cohort per node — is what
a real deployment gets today. Written down rather than papered over with an
invented endpoint; ticketed separately.

**The real stack.** This is verified against synthetic CDRs over real sockets.
Gap G6 — running it against deployed CDRs with plan.pdhc and ips.pdhc in the
loop — remains open and is deployment work.
