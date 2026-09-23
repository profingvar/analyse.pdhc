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
    for run in node_runs:
        src = run["node_id"]
        out.sources.append(SourceStatus(
            source=src, ok=True, n_patients=run.get("n_patients", 0),
            pooled=run.get("may_pool", True)))
        if run.get("may_pool", True):
            poolable.append(run)
        else:
            out.notes.append(
                f"'{src}' does not permit pooling; its figures are shown "
                f"only for that source and are not in the combined total.")

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
