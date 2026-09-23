"""Canonicalisation and hashing — the reproducibility contract (#644).

Every result carries the spec hash, the coordinator and node versions, and
the data snapshot times. That is what makes a figure in a report traceable
back to the question that produced it, months later, when the underlying data
has moved on.

For that to mean anything the hash must be a property of the SPEC'S MEANING,
not of how it happened to be written. Two specs that ask the same question
must hash the same whether they arrived as YAML or JSON, with keys in any
order, from any process, on any run.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import AnalysisSpec

#: Bumped only when canonicalisation itself changes. A different algorithm
#: version means hashes are not comparable across it, and a stored result's
#: hash must be read with the algorithm that produced it.
CANONICAL_VERSION = 1


def canonical_dict(spec: AnalysisSpec) -> dict[str, Any]:
    """The spec as plain data, with defaults made explicit.

    ``exclude_none`` is deliberately NOT used: a field explicitly set to null
    and a field omitted mean the same thing here, and dropping nulls would
    make two identical questions hash differently depending on how they were
    written. Defaults ARE included, so a spec that relies on a default and one
    that states it agree — otherwise changing a default later would silently
    re-hash every stored spec.
    """
    return spec.model_dump(mode="json", by_alias=True, exclude_none=False)


def canonical_json(spec: AnalysisSpec) -> str:
    """Canonical serialisation: sorted keys, no incidental whitespace, UTF-8.

    ``sort_keys`` removes key-order as a variable. ``separators`` removes
    whitespace. ``ensure_ascii=False`` keeps Swedish text as itself rather
    than as escapes, so the bytes match what a human would read — the hash is
    over UTF-8 bytes either way, but the canonical form stays legible when
    someone needs to see what was hashed.
    """
    return json.dumps(
        canonical_dict(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def spec_hash(spec: AnalysisSpec) -> str:
    """``sha256:<hex>`` over the canonical form.

    Prefixed with the algorithm so a stored hash stays readable if the
    algorithm is ever changed, rather than becoming an anonymous hex string
    nobody can verify.
    """
    digest = hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def provenance(spec: AnalysisSpec, *, coordinator_version: str,
               node_versions: dict[str, str] | None = None,
               snapshots: dict[str, str] | None = None) -> dict[str, Any]:
    """The block attached to every result so a figure can be reproduced.

    Args:
        coordinator_version: the coordinator that merged the partials.
        node_versions: source id -> the node version that computed it. Nodes
            can legitimately differ in version mid-rollout, and a result
            computed across two versions should say so rather than imply one.
        snapshots: source id -> ISO timestamp of that source's data snapshot.
            Per source, not one global time: federated sources are read at
            different moments and a single timestamp would be a fiction.
    """
    return {
        "spec_hash": spec_hash(spec),
        "canonical_version": CANONICAL_VERSION,
        "spec_version": spec.spec_version,
        "purpose": spec.purpose.value,
        "linkage": spec.linkage.value,
        "sources": list(spec.sources),
        "coordinator_version": coordinator_version,
        "node_versions": dict(node_versions or {}),
        "snapshots": dict(snapshots or {}),
    }
