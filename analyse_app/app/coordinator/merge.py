"""Merge orchestration (#651).

Takes what the nodes returned and produces the answer, pooled AND per source,
with disclosure control applied AGAIN over the merged figures.

Three things this has to get right:

1. A node that is offline DEGRADES that source. It does not fail the run.
   Losing Uppsala should not mean losing the Östergötland figures too; the
   result says which sources answered, so a reader is never shown a pooled
   number without knowing what is in it.
2. A node whose policy forbids pooling is EXCLUDED from the pooled figure and
   reported on its own. Its organisation permitted a figure attributable to
   them, not a contribution to someone else's total.
3. Disclosure runs again after merging, under the STRICTEST contributing
   policy. A merge of individually-safe partials can be unsafe, and no single
   node could have seen that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.engine import REGISTRY
from app.engine.base import Partial
from app.privacy.disclosure import DisclosurePolicy
from app.spec import AnalysisSpec, provenance


@dataclass
class SourceStatus:
    source: str
    ok: bool
    reason: str | None = None
    n_patients: int = 0
    pooled: bool = True


@dataclass
class RunResult:
    results: list[dict[str, Any]] = field(default_factory=list)
    sources: list[SourceStatus] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "results": self.results,
            "sources": [s.__dict__ for s in self.sources],
            "provenance": self.provenance,
            "notes": list(self.notes),
        }


def combine(spec: AnalysisSpec, node_runs: list[dict[str, Any]], *,
            coordinator_version: str,
            node_policies: dict[str, DisclosurePolicy] | None = None,
            snapshots: dict[str, str] | None = None,
            failures: dict[str, str] | None = None) -> RunResult:
    out = RunResult()
    policies = node_policies or {}
    failures = failures or {}

    for source, reason in failures.items():
        out.sources.append(SourceStatus(source=source, ok=False, reason=reason))

    poolable: list[dict[str, Any]] = []

    # #725: a node explains, in words, why it returned less than was asked of
    # it — an operator it could not evaluate, a variable with no dated
    # observations, an analysis it does not implement. Those sentences were
    # computed, serialised and then dropped here, because this loop read
    # everything from a run EXCEPT its notes. The result was a run that came
    # back empty with nothing anywhere to say why.
    #
    # A note is not a failure. The source answered; it is still ok=True and
    # still pooled. Only the explanation travels.
    #
    # Node notes must stay free of patient counts, since they are shown
    # verbatim and bypass the disclosure pass that guards the figures. Every
    # note the node can currently emit is structural, and the one number
    # among them counts GROUPS, which the analyst declared. Audited
    # 2026-09-30; keep it that way when adding one.
    note_sources: dict[str, list[str]] = {}

    for run in node_runs:
        src = run["node_id"]
        out.sources.append(SourceStatus(
            source=src, ok=True, n_patients=run.get("n_patients", 0),
            pooled=run.get("may_pool", True)))
        for note in run.get("notes") or []:
            note_sources.setdefault(note, []).append(src)
        if run.get("may_pool", True):
            poolable.append(run)
        else:
            out.notes.append(
                f"'{src}' does not permit pooling; its figures are shown "
                f"only for that source and are not in the combined total.")

    # One line per distinct note. Three sources hitting the same problem is
    # one problem, not three, and repeating it verbatim buries the one note
    # that came from a single source.
    n_runs = len(node_runs)
    for note, srcs in note_sources.items():
        where = ("Every source" if len(srcs) == n_runs and n_runs > 1
                 else f"'{srcs[0]}'" if len(srcs) == 1
                 else ", ".join(f"'{s}'" for s in srcs))
        out.notes.append(f"{where}: {note}")

    if failures:
        out.notes.append(
            f"{len(failures)} of {len(failures) + len(node_runs)} sources did "
            f"not answer. The figures below cover only the sources listed as "
            f"available.")

    # Strictest contributing policy governs the merged result.
    merged_policy = DisclosurePolicy()
    for src in [r["node_id"] for r in poolable]:
        if src in policies:
            merged_policy = merged_policy.stricter_of(policies[src])

    # Group partials by analysis kind across sources.
    by_kind: dict[str, list[Partial]] = {}
    for run in poolable:
        for blob in run.get("partials", []):
            p = Partial.from_json(blob)
            by_kind.setdefault(p.kind, []).append(p)

    for kind, parts in by_kind.items():
        module = REGISTRY.get(kind)
        if module is None:
            continue
        merged = module.merge(parts)
        result = module.finalize(merged, merged_policy)
        out.results.append(result.to_json())

    out.provenance = provenance(
        spec, coordinator_version=coordinator_version,
        node_versions={r["node_id"]: r.get("version", "unknown")
                       for r in node_runs},
        snapshots=snapshots or {})
    out.provenance["k_min_applied"] = merged_policy.k_min
    return out
