# ADR-0002 — discovery may read sibling repos, read-only

Status: accepted · Date: 2026-09-23 · Decided by: operator · Ticket: #642 (AN-0)

## Context

The reconstruction brief §0 says: "Work only inside [the repo]. Do not read,
modify or reference any other folder in the workspace."

Its §2 requires Phase 0 discovery to document how observations are modelled, how
patient identity is stored, how consent/objection/spärr are represented and
checked, how the read service behaves, and how the read log is written.

None of that lives in analyse.pdhc. It lives in cdr.pdhc, sso.pdhc, ips.pdhc and
gateway/request.pdhc. The two instructions cannot both be satisfied: Phase 0 is
impossible as written.

## Decision

Discovery may **read** sibling repositories. Edits remain confined to
analyse.pdhc.

## Alternatives considered

- **Obey §0 literally.** Rejected: DISCOVERY.md would be guesswork, and the
  brief itself demands citations.
- **Copy the needed sources into analyse.pdhc.** Rejected: creates a stale
  duplicate of another service's contract — the exact drift the platform has
  been bitten by before.
- **Ask the operator to paste the relevant code.** Rejected as needless
  friction for a read the operator has now authorised.

## Consequences

- DISCOVERY.md (#642) cites real `file:line` in sibling repos rather than
  describing them second-hand.
- The standing platform rule still holds: **no modification of another
  service's code.** Where analyse needs something the read service does not
  offer (purpose=analysis, bulk read, column projection), the answer is a
  written proposal and a ticket against that service — never a local workaround
  and never an edit made from here.
- Sibling repos may be behind or ahead of what is deployed. Discovery states
  which it inspected, and verifies against the running platform where the answer
  matters.
