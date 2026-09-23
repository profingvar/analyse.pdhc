"""Synthetic multi-source environment (#653).

What this gives you: N sources, each with its own policy and its own slice of
a synthetic population, driven through the REAL node code path — policy check,
spärr, read, pseudonymise, project, coarsen, compute, suppress — and merged by
the REAL coordinator. Only the transport is substituted.

What it does NOT give you: containerised CDRs. See the note at the bottom of
this module and the ticket; that is deployment work, not a fixture.

Synthetic identity rules (the brief, §0): no names, no addresses, no
valid-looking personnummer. Patients are ``TEST-P00001``-style ids, which the
AN-7 scanner deliberately flags, so this data can never be mistaken for real
in an output.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from app.node import NodePolicy
from app.privacy import ProjectKey

#: Deliberately unmistakable. The scanner flags TEST- ids on sight, so
#: synthetic data reaching a real output is caught rather than blending in.
PATIENT_PREFIX = "TEST-P"


@dataclass
class SynthSource:
    node_id: str
    rows: list[dict[str, Any]]
    policy: NodePolicy
    blocked: set[str] = field(default_factory=set)

    def reader(self):
        return _SynthReader(self.rows, self.blocked)


class _SynthReader:
    """Stands in for NodeReader. Same surface, no network."""

    def __init__(self, rows, blocked):
        self.rows = rows
        self.blocked = set(blocked)
        self.asked_for: list[str] | None = None

    def excluded_by_spärr(self, guids, ips_base_url):
        return {g for g in guids if g in self.blocked}

    def read_observations(self, *, purpose, patient_guids, **kw):
        self.asked_for = sorted(patient_guids)
        wanted = set(patient_guids)
        return [r for r in self.rows if r["patient_guid"] in wanted]


def _policy(node_id: str, **over) -> NodePolicy:
    text = (f"node_id: {node_id}\n"
            f"cdr_base_url: http://127.0.0.1:9046\n"
            f"permitted_purposes: [statistics, quality_registry, research]\n"
            f"k_min: {over.get('k_min', 5)}\n"
            f"may_pool: {'true' if over.get('may_pool', True) else 'false'}\n")
    return NodePolicy.load(text, is_text=True)


def build(*, nodes: int = 3, patients: int = 2000, seed: int = 0,
          blocked_fraction: float = 0.02,
          obs_per_patient: tuple[int, int] = (1, 12),
          concept: str = "x",
          **policy_over) -> list[SynthSource]:
    """N sources holding disjoint slices of one synthetic population."""
    rnd = random.Random(seed)
    sources: list[SynthSource] = []
    per = max(1, patients // nodes)

    for i in range(nodes):
        rows: list[dict[str, Any]] = []
        ids = [f"{PATIENT_PREFIX}{i:02d}{j:05d}" for j in range(per)]
        for pid in ids:
            # A per-patient level plus noise, so a mean is meaningful and an
            # SD is not degenerate.
            level = rnd.gauss(10, 3)
            for _ in range(rnd.randint(*obs_per_patient)):
                rows.append({
                    "patient_guid": pid,
                    # #696: real rows carry the concept, and cohort criteria
                    # select on it. The harness lacked the field entirely,
                    # which is why nothing noticed that spec.cohort was never
                    # applied — there was nothing for it to match against.
                    "concept": concept,
                    "value": round(level + rnd.gauss(0, 1.5), 4),
                    "unit": "L",
                    "effective_at": None,
                    "meta": {"author_org": f"org-{i}"},
                    "demographics": {"sex": rnd.choice(["female", "male"])},
                })
        blocked = {p for p in ids if rnd.random() < blocked_fraction}
        sources.append(SynthSource(node_id=f"cdr{i + 1}", rows=rows,
                                   policy=_policy(f"cdr{i + 1}",
                                                  **policy_over),
                                   blocked=blocked))
    return sources


def run(spec, sources: list[SynthSource], *,
        project_key: ProjectKey | None = None,
        coordinator_version: str = "0.1.0-synth"):
    """Drive the real node path on every source, then the real coordinator."""
    from app.coordinator import combine
    from app.node import run_spec

    key = project_key or ProjectKey("synth", b"s" * 32)
    runs, failures, policies = [], {}, {}
    for src in sources:
        policies[src.node_id] = src.policy.disclosure()
        try:
            cohort = sorted({r["patient_guid"] for r in src.rows})
            nr = run_spec(spec, src.policy, src.reader(), project_key=key,
                          ips_base_url="http://ips", cohort=cohort)
            runs.append(nr.to_json())
        except Exception as e:                    # a source that refuses
            failures[src.node_id] = str(e)
    return combine(spec, runs, coordinator_version=coordinator_version,
                   node_policies=policies, failures=failures)


# ── what a REAL multi-CDR environment additionally needs ──────────────
#
# Not built here, and scoped rather than hand-waved:
#
#   * one cdr.pdhc container + its own Postgres per source (ports are free
#     in the 9110-9119 block for the NODES; the CDRs need their own)
#   * plan.pdhc reachable, because concepts and units are resolved from it
#   * ips.pdhc reachable, because spärr and the consent verdict live there
#     and both fail CLOSED — with ips absent, a real node reads nothing at
#     all, which is correct and makes the stack all-or-nothing
#   * a service key per CDR, registered so the node is a known caller
#   * seeding, for which sim.pdhc already generates synthetic longitudinal
#     data from real PlanDefinitions
#
# That is most of the platform, which is why it is deployment work rather
# than a test fixture. The harness above exercises the code path; the stack
# exercises the wiring, and they are different risks.
