# #807 — per-CDR node topology: deployment plan

Drafted 2026-10-19. Implements ADR-0005. Supersedes nothing; #807's body stays
the requirement and this is how to satisfy it.

Every number below was measured, not assumed. Re-measure before executing —
this platform's plans have gone stale in days.

---

## 1. What changed since #807 was written

#807's gate reads: *"'When there is data to federate' is still the precondition
and is still unmet."* **It is now partly met.** Measured read-only 2026-10-19:

| CDR | `observation` rows | distinct patients | ips-known | clinic-assigned | node-worthy |
|---|---|---|---|---|---|
| cdr1 | 1,942 | 3 | — | — | marginal |
| **cdr2** | **19,490** | **30** | **30/30** | **30/30** | **yes** |
| **cdr3** | **19,509** | **30** | 30 | 30 | **yes** |
| cdr_6 | 0 | 0 | — | — | no |

Data landed 2026-10-13 from `sim.pdhc`, four days after #807 was filed, by the
route #806 nominated as cheapest. cdr2's and cdr3's cohorts are **different
patients**, which is what makes a fan-out test the merge rather than
deduplication.

So the question #807 could not previously ask is now live: **two CDRs can back
a real node, two cannot.**

## 2. Decision: deploy two nodes, not four

ADR-0005 decides one node per CDR, and that stands. But #806 is explicit:

> Do not stand up nodes over empty databases to make this go green. A federated
> run across empty sources proves nothing, and a green light wired to nothing is
> worse than a red one.

cdr_6 holds **zero** rows (purged 2026-10-09) and cdr1 holds 3 patients and is
deliberately absent from `CDR_ENDPOINTS` because gateway feeds it. Deploying
nodes for either would produce exactly the green-wired-to-nothing #806 warns
about.

**This is a phased rollout of ADR-0005's design, not a deviation from it.**
Ports are assigned by CDR identity, so the deferred two slot in later without
renumbering anything:

| Port | CDR | CDR port | Phase |
|---|---|---|---|
| 9112 | cdr1 | 9046 | deferred — 3 patients, gateway-fed |
| **9113** | **cdr2** | 9146 | **now** |
| **9114** | **cdr3** | 9246 | **now** |
| 9115 | cdr_6 | 9055 | deferred — 0 rows |
| 9116–9119 | — | — | spare (ADR-0005) |

Verified 2026-10-19: 9112–9119 all free.

## 3. Three traps, stated before the steps

**(a) `ANALYSE_ROLE` defaults to the dangerous value.** `app/__init__.py:150`:

```python
app.config.setdefault("ANALYSE_ROLE", os.environ.get("ANALYSE_ROLE", "both"))
```

and line 223 registers the node surface for both `"node"` and `"both"`. So an
instance that loses the variable serves `POST /api/v1/node/run` — which #807
calls "a second, quieter way into the data, reachable by anyone holding the
transport secret". Production analyse is correctly `coordinator`; **every node
must set `node` explicitly** and the coordinator must never be left to default.
Assert it after each deploy rather than trusting the file.

**(b) A mismatched project key fails silently.** From #807: *"A pseudonym
computed under a different key is a different patient to the merge, and the
failure is silent — counts come out too high."* Nodes must therefore be **given**
`ANALYSE_PROJECT_KEY` by copying, never generate their own. Gate every node on a
digest comparison (step 6.2); do not make it a habit, make it a check.

**(c) `ANALYSE_ALLOW_LIVE_DATA` stays UNSET.** It only matters for
`data_mode: live`, and all four policies are `synthetic`. The code comment is
blunt about why: it must not be possible to point this tool at real patients by
forgetting a flag.

## 4. Policy files — one per node, owned by the organisation behind that CDR

Schema read from `app/node/policy.py`. Known keys are exactly:
`node_id`, `cdr_base_url`, `source_clinic_id`, `permitted_purposes`,
`permitted_analyses`, `k_min`, `may_pool`, `may_use_other_orgs_rows`,
`data_mode`. **An unknown key is a loud `PolicyError`, not a warning** — the
module's comment: *"a misspelled key in a policy file is a control the
organisation believes it has and does not."*

Required: `node_id`, `cdr_base_url`. Valid `permitted_purposes`:
`research`, `statistics`, `quality_registry`. Valid `permitted_analyses` ⊆
`describe, histogram, frequency, correlation, compare_groups, over_time,
completeness`. `k_min` defaults to 5 (`DEFAULT_K_MIN`).

`/usr/local/www/analyse.pdhc/shared/node_policy_cdr2.yaml`:

```yaml
node_id: cdr2
# Public subdomain, not loopback (#830). Reachable over TLS from inside the
# container, which is where this runs.
cdr_base_url: https://cdr2.pdhc.se
permitted_purposes:
  - research
  - statistics
permitted_analyses:
  - describe
  - histogram
  - frequency
  - completeness
k_min: 5
may_pool: true
# Rows AUTHORED elsewhere but stored here stay out until an agreement says
# otherwise. Answerable at all only since cdr #665 added author_org_guid.
may_use_other_orgs_rows: false
data_mode: synthetic
```

`node_policy_cdr3.yaml` is identical but for `node_id: cdr3` and
`cdr_base_url: https://cdr3.pdhc.se`.

Deliberately omitted: `source_clinic_id`. All 30 of cdr2's patients are in **one**
organisation (measured), so pinning a clinic would narrow an already-single-org
cohort to no visible effect while looking like a control. Set it when the
population spans organisations — see §7.

Mode 600, owner `miserver`, in `shared/` so a release swap cannot delete them
(§7: release-local config is a trap, and analyse's `.env` was moved to `shared/`
for this reason in #758).

## 5. Steps

**5.1 — secrets (operator, done or ready).** `~/install_analyse_secrets.sh`
generates `ANALYSE_TRANSPORT_SECRET` and `ANALYSE_PROJECT_KEY` server-side,
installs them into `shared/.env`, and prints only length + sha256 prefix.
Neither value may be echoed: the first attempt at this printed a candidate
secret to a terminal, which is a transcript.

**5.2 — policy files.** Write the two YAML files above. Validate each BEFORE
deploying anything:

```
docker exec analyse_pdhc_app python -c "
from app.node.policy import NodePolicy
p = NodePolicy.load('/app/shared/node_policy_cdr2.yaml')
print(p.node_id, p.cdr_base_url, p.k_min, p.data_mode, sorted(p.permitted_analyses))"
```

A `PolicyError` here is the cheapest possible failure. Do not skip it.

**5.3 — two node containers**, from the SAME image as the coordinator
(ADR-0005: one deployable, role by configuration). Each node needs:

```
ANALYSE_ROLE=node                  # NEVER left to default — see trap (a)
APP_PORT=9113                      # 9114 for cdr3
ANALYSE_NODE_POLICY=/app/shared/node_policy_cdr2.yaml
ANALYSE_NODE_COHORT=<cohort source, see app/node/cohort_source.py>
ANALYSE_TRANSPORT_SECRET=<copied, identical to coordinator>
ANALYSE_PROJECT_KEY=<copied, identical to coordinator>
ANALYSE_PDHC_SERVICE_KEY=<analyse's existing 64-char key, to read the CDR>
PLAN_BASE_URL=https://plan.pdhc.se
IPS_BASE_URL=https://ips.pdhc.se
IPS_API_KEY=<analyse's own, #730>
# ANALYSE_ALLOW_LIVE_DATA deliberately absent — trap (c)
```

Nodes are **stateless** — `app/node/service.py` touches no database — so no
extra Postgres instances and no volumes. Bind `127.0.0.1` only (§3).

**5.4 — the coordinator learns about them.** `ANALYSE_NODES` is
`node_id=url` comma-separated (`app/transport/client.py::endpoints_from_config`),
the same shape as `CDR_ENDPOINTS`:

```
ANALYSE_NODES=cdr2=http://host.docker.internal:9113,cdr3=http://host.docker.internal:9114
```

These are loopback by necessity, not drift: the nodes have no DNS record and no
vhost, which is the same deliberate exception §7 records for termbank and cdr_6.
**Record it there** rather than leaving the next #830-style sweep to rediscover
it. Prefer `ANALYSE_NODE_TIMEOUT` set explicitly over its default.

**5.5 — one recreate, not four.** `.env` is read at container-create time, so
`docker restart` keeps the old environment (a recorded trap). Set every variable
first, then `docker-compose up -d` once.

## 6. Verification — what makes this real rather than green

**6.1 — the node surface is where it should be, and only there.**

```
coordinator:  POST /api/v1/node/run  ->  404     (ANALYSE_ROLE=coordinator)
each node:    POST /api/v1/node/run  ->  401/403 unsigned, 200 signed
```

A 200 on the coordinator is trap (a) having fired. Assert this; it is the one
check that would catch a lost variable.

**6.2 — the project key matches on all three.** Gate on it:

```
bash ~/install_analyse_secrets.sh --show-digest     # coordinator and each node
```

Identical `ANALYSE_PROJECT_KEY` digests, or stop. This is the silent failure.

**6.3 — a fan-out across two populated nodes.** #806's acceptance: *"a query
fans out to more than one real CDR-backed node and the merged result matches
what the same data produces pooled."* With 30 + 30 distinct patients the merged
cohort should be 60; if it comes out higher, suspect 6.2 before anything else.

Note `k_min: 5` against 30 patients per source: many cells will be suppressed,
and suppression is correct behaviour, not a failure. Decide what the acceptance
query is before running it, so a suppressed result is not read as a broken one.

**6.4 — the gates observably applied.** #806 requires "a patient excluded at one
node can be shown to be excluded, not merely absent". **All 30 of cdr2's
patients are in one organisation**, so org-based exclusion cannot be
demonstrated on this cohort — the same single-org limit that holds #818.
Per-patient consent exclusion can be: revoke consent for one known patient in
ips, re-run, and show that patient excluded by name at that node. Choose which
gate the acceptance run demonstrates, and say which one it did not.

**6.5 — fail-closed is still fail-closed.** plan and ips both fail closed, so
with ips absent a node reads nothing. #806 calls this "all-or-nothing, which is
itself worth verifying deliberately rather than discovering." Point one node's
`IPS_BASE_URL` at a dead host and require it to read zero rows rather than all
of them.

## 7. Deliberately deferred

- **Nodes for cdr1 and cdr_6** — 3 patients and 0 rows. Ports 9112 and 9115 are
  reserved for them.
- **`source_clinic_id` in the policies** — single-org population makes it
  decorative today.
- **`data_mode: live`** — all policies synthetic; `ANALYSE_ALLOW_LIVE_DATA`
  stays unset.
- **Nodes beside their own CDR on separate hosts**, as ADR-0005 envisages. All
  four CDRs share one Colima VM, so "beside" is logical, not physical. Worth
  stating so nobody reads this topology as the trust boundary it is meant to
  model.

## 8. Rollback

Nodes are stateless, so rollback is removing them: stop the two containers,
unset `ANALYSE_NODES`, `docker-compose up -d` the coordinator. No data moves and
no migration runs. Back up `shared/.env` first, as
`~/install_analyse_secrets.sh` already does.

The only irreversible step is **rotating `ANALYSE_PROJECT_KEY`** once pseudonyms
exist under it: every stored pseudonym becomes a different patient. Treat the
first installed value as permanent for the project's life.
