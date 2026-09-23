# Node policy — the operator's reference

Ticket #654 · One file per node, owned by **the organisation behind that CDR**.

A node runs beside one CDR. The organisation responsible for those rows
decides what may be asked of them, and **the coordinator cannot override any
of it**. A policy a coordinator could relax would be a suggestion.

## The file

```yaml
node_id: cdr_uppsala                       # required
cdr_base_url: http://127.0.0.1:9046        # required

# Purposes this organisation permits. From the PLATFORM's closed enum:
#   research | statistics | quality_registry
# An EMPTY or absent list means this node answers NOTHING.
permitted_purposes:
  - statistics
  - quality_registry

# Analysis types permitted. Absent means all of:
#   describe, histogram, frequency, correlation,
#   compare_groups, over_time, completeness
permitted_analyses:
  - describe
  - frequency
  - completeness

# This organisation's minimum cell size. A coordinator may ask for STRICTER
# and get it; asking for looser silently yields this value.
k_min: 5

# May this node's figures be combined into a pooled total, or may they only
# be shown attributed to this source?
may_pool: true

# May rows AUTHORED by another organisation, but stored in this CDR, be used?
# Answerable at all only since cdr #665 added author_org_guid.
may_use_other_orgs_rows: false

# synthetic | live. Live also requires an explicit allow_live at run time.
data_mode: synthetic
```

## Why the defaults lean the way they do

**No stated purposes means the node answers nothing.** A policy someone
created and forgot to fill in should refuse, not permit. Every other default
here is permissive-looking because it is safe; this one is not, so it fails
closed.

**An unknown key fails the load.** `k_minimum: 5` does not quietly become
nothing — it raises. A misspelled key is a control the organisation believes
it has and does not, which is worse than having no control at all.

**`may_use_other_orgs_rows` defaults to `false`.** A CDR may physically hold
rows authored elsewhere. Using them is a decision for the authoring
organisation, not the storing one, so the storing organisation cannot grant it
by inaction.

**`data_mode` defaults to `synthetic`.** It must not be possible to point this
tool at real patients by forgetting a flag.

## What the node does with it

Checked **before anything is read**:

1. the spec's `purpose` is in `permitted_purposes`, else refuse
2. every analysis type is in `permitted_analyses`, else refuse
3. `data_mode` is synthetic, or `allow_live` was passed deliberately

Then, per run: `k_min` is applied at the node, and again at the coordinator
under the **strictest** contributing policy. `may_pool: false` keeps this
node's figures out of the pooled total; they are reported for this source
alone, and the result says so in words.

## The audit trail

Every run writes **two** rows: one to the platform log, and one locally on the
node. The local one is the point — an organisation whose rows sit in another
organisation's CDR can still see that its data was used.

Counts in the audit are written **suppressed**. An audit log is read by more
people than a result is, and a per-source count of 3 discloses as much sitting
in an audit row as it would in a table.
