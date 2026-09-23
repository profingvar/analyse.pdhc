# ADR-0006 — `author_org` is a distinct field and must be added

Status: accepted · Date: 2026-09-23 · Decided by: operator
Tickets: #665 (cdr.pdhc), #666 (gateway.pdhc) · Discovery gap: G3

## Context

The reconstruction brief states that every observation is "tagged with
`author_org` (the organisation that created it) and `provider_org` (the
organisation that technically submitted it)", and that "each organisation
remains responsible for its own rows".

Discovery (#642) found no such field. What exists is:

| Field | Meaning | Where |
|---|---|---|
| `requesting_org_guid` | who **ordered** the data | `ClinicalContext` |
| `provider_org_guid` | who **submitted** it (authenticated PAT holder) | `ClinicalContext` |
| `org_guid` | a single org column | per-type FHIR tables |

Worse, author and submitter are actively **conflated**: gateway.pdhc writes the
authenticated provider org *both* as `clinical_context.provider_org_guid` and as
the FHIR Observation `performer` — and `performer` in FHIR means *who is
responsible for the observation*, i.e. the author. The cdr backfill then reads
`performer` back into `provider_org_guid`, closing the loop.

## Decision

`requesting_org_guid` is **not** `author_org`. A distinct `author_org_guid` is
added to the platform.

Rejected explicitly: reusing `requesting_org_guid`. Who *ordered* data is not
who *created* it, and equating them would have made the node policy silently
wrong rather than absent — a worse failure, because it would look like it
worked.

## Consequences

- Two tickets, in order: **#665** (cdr.pdhc — the column, ingest mapping,
  backfill policy) then **#666** (gateway.pdhc — accept a declared value,
  forward it, reconsider the `performer` mapping).
- **AN-5's node policy** gains the setting the brief specifies — *may rows
  authored by other organisations stored in this CDR be used* — which is
  unimplementable until #665 lands. AN-5 must not fake it by substituting
  `provider_org_guid`.
- **Backfill: NULL, not a default.** Existing rows get no value. A defaulted
  `author_org = provider_org` is a guess that becomes indistinguishable from a
  declared fact the moment it is written. A consumer needing a value can
  coalesce; a consumer reading a backfilled guess cannot tell it apart.
- **Historical `performer` is not rewritten.** Those rows carry
  submitter-as-performer, and that is the only provenance they have.

### The asymmetry to keep documenting

`provider_org_guid` is **authenticated** — it comes from the PAT and the
submitter cannot lie about it. `author_org_guid` can only be **declared**,
because only the submitter knows who authored.

So author is *weaker evidence* than provider, and any analysis filtering on it
is trusting the submitter's declaration. That difference must be visible
wherever `author_org` is exposed — in the node policy documentation, and in any
UI that lets someone filter or group by it. It is the kind of distinction that
is obvious now and invisible in two years.
