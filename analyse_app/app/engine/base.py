"""The three-function contract every analysis type implements (#647).

    local(rows)        -> Partial     on the node
    merge([Partial])   -> Partial     on the coordinator
    finalize(Partial)  -> Result      on the coordinator

A ``Partial`` carries SUFFICIENT STATISTICS and nothing else. That is the
property the whole architecture rests on: what crosses the wire is enough to
compute the answer and not enough to reconstruct a patient. Every partial here
is a plain dict, so it serialises without a conversion layer and so that what
leaves a node is legible to a human reviewing it.

``Result`` carries an ``exactness`` flag. A federated figure is *exact* when
merging partials gives the same number as pooling the raw data would have —
true for anything built from sums and counts — and *approximate* when it does
not, which is true of anything needing ranks or order statistics across
nodes. The UI must show the difference, so it is on the result, not in a
comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EXACT = "exact"
APPROXIMATE = "approximate"


@dataclass
class Partial:
    """Sufficient statistics from one source, or merged across sources."""
    kind: str
    data: dict[str, Any]
    #: Which CDR produced this. None once merged across several.
    source: str | None = None
    #: Per-source partials retained through the merge, so every result can be
    #: given pooled AND per source without a second pass.
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "data": self.data, "source": self.source,
                "by_source": self.by_source}

    @classmethod
    def from_json(cls, blob: dict[str, Any]) -> "Partial":
        return cls(kind=blob["kind"], data=blob["data"],
                   source=blob.get("source"),
                   by_source=blob.get("by_source") or {})


@dataclass
class Result:
    kind: str
    pooled: dict[str, Any]
    by_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    exactness: str = EXACT
    #: Why a figure is approximate, or why something was withheld. Shown to
    #: the reader, so it is written in plain language, not as a code.
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "pooled": self.pooled,
                "by_source": self.by_source, "exactness": self.exactness,
                "notes": list(self.notes)}


def _merge_by_source(partials: list[Partial]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in partials:
        if p.source is not None:
            out[p.source] = p.data
        out.update(p.by_source)
    return out
