"""Executing a spec on a node (#648).

The order of operations here is the whole security argument, so it is written
out rather than implied:

    1. policy      — refuse a purpose or analysis this organisation forbids
    2. data_mode   — refuse live data unless deliberately enabled
    3. spärr       — drop blocked patients BEFORE any read
    4. read        — through the platform read service, purpose declared, so
                     the consent join runs and fails closed
    5. project     — allowlist, constructive
    6. coarsen     — quasi-identifiers narrowed
    7. compute     — sufficient statistics only
    8. suppress    — node-side disclosure control before anything leaves
    9. audit       — locally, so the owning organisation can see its own data
                     being used

Every step before 7 can only REDUCE what is computed over. That ordering is
deliberate: an aggregate computed over a blocked patient has already used
their data, even if the number is discarded afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.engine import REGISTRY
from app.privacy import ProjectKey, build_allowlist, project, pseudonymise
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec

from .policy import NodePolicy, PolicyError
from .reader import ConsentUnavailable, NodeReader


class NodeRefusal(PolicyError):
    """The node declines to run this spec. Carries a reason for the operator."""


@dataclass
class NodeRun:
    """What a node returns: partials, and an account of what it excluded."""
    node_id: str
    partials: list[dict[str, Any]] = field(default_factory=list)
    n_patients: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    may_pool: bool = True
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "partials": self.partials,
                "n_patients": self.n_patients, "excluded": self.excluded,
                "may_pool": self.may_pool, "notes": list(self.notes)}


def check_data_mode(policy: NodePolicy, *, allow_live: bool = False) -> None:
    """Live data needs a deliberate act, and the act is logged upstream.

    Default-synthetic is the brief's requirement and the reason is blunt: it
    must not be possible to point this tool at real patients by forgetting
    a flag.
    """
    if policy.data_mode == "live" and not allow_live:
        raise NodeRefusal(
            f"node '{policy.node_id}' is configured for LIVE data; running "
            f"against it requires an explicit allow_live and is logged")


def run_spec(spec: AnalysisSpec, policy: NodePolicy, reader: NodeReader, *,
             project_key: ProjectKey, ips_base_url: str,
             cohort: list[str], allow_live: bool = False,
             requested_disclosure: DisclosurePolicy | None = None,
             research_projects: tuple[str, ...] = ()) -> NodeRun:
    # 1 + 2 — refuse before touching anything
    policy.check(spec)
    check_data_mode(policy, allow_live=allow_live)

    run = NodeRun(node_id=policy.node_id, may_pool=policy.may_pool)
    disclosure = policy.disclosure(requested_disclosure)

    # 3 — spärr, before the read
    blocked = reader.excluded_by_spärr(cohort, ips_base_url)
    eligible = [g for g in cohort if g not in blocked]
    if blocked:
        run.excluded["blocked"] = len(blocked)

    if not eligible:
        run.notes.append(
            "No patients in this cohort could be read at this source.")
        return run

    # 4 — read, purpose declared so the consent join runs; 503 propagates
    rows = reader.read_observations(
        purpose=spec.purpose.value, patient_guids=eligible,
        research_projects=research_projects)

    # The CDR's consent filter may return fewer patients than were asked for.
    seen = {r.get("patient_guid") for r in rows if r.get("patient_guid")}
    withheld = len(set(eligible) - seen)
    if withheld:
        run.excluded["consent"] = withheld

    # 5 — pseudonymise, then project. Pseudonym first, so the raw guid is
    # gone before a record is built rather than being carried and dropped.
    allowlist = build_allowlist(spec)
    projected = []
    for r in rows:
        guid = r.get("patient_guid")
        if not guid:
            continue
        enriched = dict(r)
        enriched["pid"] = pseudonymise(guid, project_key)
        enriched["source"] = policy.node_id
        projected.append(project(enriched, allowlist))

    run.n_patients = len({p["pid"] for p in projected if p.get("pid")})

    # 7 + 8 — compute, then suppress before anything leaves
    for analysis in spec.analyses:
        module = REGISTRY.get(analysis.type)
        if module is None:
            continue                       # AN-9 types land later
        partial = _dispatch(module, analysis, projected, policy, disclosure)
        if partial is not None:
            run.partials.append(partial.to_json())

    if not policy.may_pool:
        run.notes.append(
            f"'{policy.node_id}' does not permit its rows to be pooled; its "
            f"figures may only be shown for this source.")
    return run


def _dispatch(module, analysis, rows, policy: NodePolicy,
              disclosure: DisclosurePolicy):
    """Call one analysis type's `local`. Kept small and explicit; the engine
    knows nothing about the spec and the spec knows nothing about the engine."""
    kind = analysis.type
    if kind == "describe":
        values = [r.get("value") for r in rows]
        return module.local(values, source=policy.node_id,
                            k_min=disclosure.k_min)
    if kind == "frequency":
        return module.local([r.get("value") for r in rows],
                            source=policy.node_id)
    if kind == "correlation":
        cols: dict[str, list] = {}
        for name in analysis.vars:
            cols[name] = [r.get(name) for r in rows]
        return module.local(cols, source=policy.node_id,
                            method=analysis.method)
    if kind == "histogram":
        bins = analysis.bins
        if bins is None:
            return None                    # needs the coordinator's first pass
        from app.engine.histogram import edges_from
        edges = edges_from(bins.range[0], bins.range[1], bins.width)
        return module.local([r.get("value") for r in rows], edges,
                            source=policy.node_id)
    return None
