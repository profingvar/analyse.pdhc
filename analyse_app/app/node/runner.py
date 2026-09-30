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
    6b. cohort     — apply the spec's inclusion criteria (#696); the list the
                     node started from is a CANDIDATE list, not the cohort
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
from app.node.frame import build as build_frame, series
from app.privacy import ProjectKey, build_allowlist, project, pseudonymise
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec

from . import cohort_criteria
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

    # 6b — COHORT. Until #696 the spec's inclusion criteria were read by
    # nothing on this path, so a figure came back labelled with a criterion
    # that had never been applied — a wider population than was asked about,
    # reported under the analyst's question. The candidate list the node
    # started from is exactly that: candidates.
    projected, excluded_by_cohort = cohort_criteria.apply(projected, spec)
    if excluded_by_cohort:
        run.excluded["cohort"] = excluded_by_cohort

    run.n_patients = len({p["pid"] for p in projected if p.get("pid")})

    if not projected:
        run.notes.append(
            "No patient at this source meets the cohort criteria.")
        return run

    # 7 + 8 — compute, then suppress before anything leaves
    # #715: the engines take WIDE per-patient records; the CDR returns LONG
    # observation rows. Nothing bridged the two, so every analysis computed
    # over every observation regardless of which variable it named.
    frame = build_frame(projected, spec)

    for analysis in spec.analyses:
        module = REGISTRY.get(analysis.type)
        if module is None:
            run.notes.append(
                f"'{analysis.type}' is not implemented at this node, so it is "
                f"not in these results.")
            continue
        try:
            partial = _dispatch(module, analysis, frame, projected, spec,
                                policy, disclosure)
        except DispatchError as e:
            run.notes.append(str(e))
            continue
        if partial is None:
            # A missing number with no explanation is worse than an error:
            # the researcher cannot tell a suppressed result from one that was
            # never computed. Before #715 this branch was a bare skip.
            run.notes.append(
                f"'{analysis.type}' produced no result at this source.")
            continue
        run.partials.append(partial.to_json())

    if not policy.may_pool:
        run.notes.append(
            f"'{policy.node_id}' does not permit its rows to be pooled; its "
            f"figures may only be shown for this source.")
    return run


class DispatchError(RuntimeError):
    """This analysis cannot be computed here, and the reason is reportable."""


def _values(frame, name):
    return [r.get(name) for r in frame]


def _dispatch(module, analysis, frame, rows, spec, policy: NodePolicy,
              disclosure: DisclosurePolicy):
    """Call one analysis type's `local`.

    `frame` is one wide record per patient; `rows` are the long observation
    rows, needed only by over_time. Every branch selects BY VARIABLE — the
    absence of that was #715.
    """
    kind = analysis.type

    if kind == "describe":
        # One Partial per analysis, so a multi-variable describe reports the
        # first named variable; the spec's own shape is one value list.
        return module.local(_values(frame, analysis.vars[0]),
                            source=policy.node_id, k_min=disclosure.k_min)

    if kind == "frequency":
        return module.local(_values(frame, analysis.vars[0]),
                            source=policy.node_id)

    if kind == "correlation":
        cols = {name: _values(frame, name) for name in analysis.vars}
        return module.local(cols, source=policy.node_id,
                            method=analysis.method)

    if kind == "histogram":
        bins = analysis.bins
        if bins is None:
            return None                    # needs the coordinator's first pass
        from app.engine.histogram import edges_from
        edges = edges_from(bins.range[0], bins.range[1], bins.width)
        return module.local(_values(frame, analysis.var), edges,
                            source=policy.node_id)

    if kind == "completeness":
        names = [v.name for v in spec.variables]
        return module.local(frame, names, source=policy.node_id)

    if kind == "compare_groups":
        groups = _group_values(frame, spec, analysis.vars[0])
        if len(groups) < 2:
            raise DispatchError(
                f"'compare_groups' needs at least two groups with members at "
                f"this source; {len(groups)} had any.")
        return module.local(groups, source=policy.node_id,
                            k_min=disclosure.k_min)

    if kind == "over_time":
        var = _variable(spec, analysis.var)
        points = series(rows, var)
        if not points:
            raise DispatchError(
                f"'over_time' has no dated observations of '{analysis.var}' at "
                f"this source, so there is nothing to plot.")
        return module.local(points, bin_days=analysis.bin_days,
                            range_days=analysis.range_days,
                            source=policy.node_id)

    return None


def _variable(spec, name):
    for v in spec.variables:
        if v.name == name:
            return v
    raise DispatchError(f"variable '{name}' is not declared in this spec.")


_GROUP_OPS = {
    "gte": lambda a, b: a >= b, "gt": lambda a, b: a > b,
    "lte": lambda a, b: a <= b, "lt": lambda a, b: a < b,
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
}


def _group_values(frame, spec, var_name):
    """`Group.where` is {variable: {op: value}} over the wide frame.

    A record missing any variable the predicate names is NOT a member. Absence
    is not a failed comparison — treating None as "did not match" would
    silently move every incompletely-recorded patient into the other group.
    """
    out: dict[str, list] = {}
    for group in spec.groups:
        members = []
        for rec in frame:
            ok = True
            for field, pred in group.where.items():
                val = rec.get(field)
                if val is None:
                    ok = False
                    break
                for op, target in pred.items():
                    fn = _GROUP_OPS.get(op)
                    if fn is None:
                        raise DispatchError(
                            f"group '{group.name}' uses an unknown operator "
                            f"'{op}'.")
                    try:
                        if not fn(float(val), float(target)):
                            ok = False
                            break
                    except (TypeError, ValueError):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                members.append(rec.get(var_name))
        if members:
            out[group.name] = members
    return out
