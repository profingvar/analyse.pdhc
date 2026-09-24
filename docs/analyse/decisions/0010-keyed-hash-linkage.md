# ADR-0010 — keyed_hash linkage, gated and limited to counting

Status: **RETIRED 2026-09-24** (was accepted 2026-09-23) · Tickets: #660
(AN-17), retired by #687

> ## Retired — the mode is gone, this record is not
>
> `keyed_hash` was removed from the `Linkage` enum on 2026-09-24 by operator
> decision (#687). `app/privacy/linkage.py` and its tests are deleted; a spec
> naming `keyed_hash` now fails validation.
>
> **Why.** It shipped as a tested mechanism that could not be used. The CDR
> holds no personnummer, the ips token path was never specified, and the
> legal basis was never established. Keeping it behind a flag meant a control
> that *looked* like a feature — and "shipped but unusable" is how something
> gets switched on by someone who did not read this file.
>
> **What is kept, and why this ADR stays.** The design below is the record of
> how it would have to work if it is ever wanted: the one-set-operation
> boundary (`deduplicated_count` returns a number, deliberately not the
> union, the overlap, or which tokens matched), and above all that **the key
> is held by the nodes and never the coordinator** — a coordinator holding it
> could compute tokens for any identity it guessed and test them against what
> the nodes returned, turning a deduplication token into an identity oracle.
> Anyone reviving this starts here, not from scratch.
>
> Reviving it needs all three of: an ips-side token path, a documented legal
> basis, and this design honoured.

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
