# ADR-0010 — keyed_hash linkage, gated and limited to counting

Status: accepted · Date: 2026-09-23 · Ticket: #660 (AN-17)

## Context

The brief describes three linkage modes. `shared_guid` and `none` shipped in
the MVP; `keyed_hash` was deferred behind a feature flag with its own ADR.

In `keyed_hash`, each node computes `HMAC(linkage_key, personnummer)` locally.
Two nodes holding the same patient produce the same token, so the coordinator
can count distinct patients across sources without either node revealing who
its patients are.

## Decision

Implemented, **behind `ANALYSE_ENABLE_KEYED_HASH`, and limited to
deduplicated counting.** Joining per-patient variables across nodes is
**refused in code**, not merely discouraged in documentation.

## The line, and why it is drawn in code

**Deduplicated counting is safe.** Two tokens matching tells the coordinator
that one patient appears in two sources. That is a fact about arithmetic.

**Joining per-patient variables across nodes is not.** It requires
patient-level data to leave a node, which needs trusted mode *and* a
documented legal basis — neither of which this mode establishes.

The distinction is one set operation wide. `deduplicated_count` returns a
number and deliberately does not return the union, the overlap, or which
tokens were shared: a coordinator holding the overlap could go back to a node
and ask about those specific patients. `join_across_nodes()` exists purely to
raise, so the attempt fails loudly and names the reason rather than someone
assembling the join from a set intersection and concluding it was permitted
because nothing stopped them.

## Who holds the key

The nodes, or a trusted third party. **Never the coordinator.** A coordinator
holding the linkage key could compute tokens for any identity it cared to
guess and test them against what the nodes returned — that turns a
deduplication token into an identity oracle.

## The constraint that makes this unusable today

AN-0 discovery, gap **G9**: **the CDR holds no personnummer.** The platform is
GUID-keyed throughout, and the CDR observation tables carry `patient_guid` and
nothing else.

So this mode cannot be served by a CDR at all. The token would have to be
computed by `ips.pdhc`, which owns patient identity — a path that does not
exist. **However the flag is set, this mode is unimplementable in production
until that path is built.** It is shipped as a tested mechanism and an honest
boundary, not as a working feature.

## Legal basis

Not established. `shared_guid` and `none` rest on the same basis as the rest
of the analysis path. `keyed_hash` involves deriving a stable token from a
national identity number, and whoever turns the flag on is asserting a basis
for that which nobody has written down yet. The flag is the place that
assertion becomes deliberate.
