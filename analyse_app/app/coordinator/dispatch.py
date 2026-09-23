"""Running a spec across real nodes (#684 / AN-12).

The join between the wire and the merge. Everything hard is in one of the two
halves it calls: `transport.fan_out` guarantees every source is accounted for,
`combine` guarantees the merged figure is disclosure-controlled under the
strictest contributing policy. This only has to not lose anything between them.

One judgement lives here. **A run where NO node answered is a failure, not an
empty result.** `combine` would happily return a result with zero sources and
a note, and that result would render as a page of suppressed cells — visually
indistinguishable from a real analysis of a small population. An analyst
reading that would conclude the cohort was tiny, when in fact nothing ran.
"""
from __future__ import annotations

from typing import Any

from app.spec import AnalysisSpec
from app.transport.client import (
    DEFAULT_MAX_WORKERS, DEFAULT_TIMEOUT, NodeEndpoint, fan_out,
)
from app.version import VERSION

from .merge import RunResult, combine
from .signing import sign


class NoSourcesAnswered(RuntimeError):
    """Every node failed. Carries the per-node reasons."""

    def __init__(self, failures: dict[str, str]):
        self.failures = dict(failures)
        detail = "; ".join(f"{k}: {v}" for k, v in sorted(self.failures.items()))
        super().__init__(
            f"no source answered, so there is no result to show — {detail}")


def run_distributed(spec: AnalysisSpec, endpoints: list[NodeEndpoint],
                    secret: bytes, *, project_id: str,
                    signing_secret: bytes | None = None,
                    k_min: int | None = None,
                    research_projects: tuple[str, ...] = (),
                    timeout: float = DEFAULT_TIMEOUT,
                    max_workers: int = DEFAULT_MAX_WORKERS,
                    coordinator_version: str = VERSION,
                    session: Any = None) -> RunResult:
    if not endpoints:
        raise NoSourcesAnswered({})

    # The spec signature is ALWAYS sent. It is a different claim from the
    # envelope's: the envelope says "the other half of this deployment sent
    # these bytes", the signature says "the coordinator approved this
    # analysis", and it is taken over the spec's CANONICAL form, so it
    # survives the spec being re-serialised anywhere in between.
    #
    # `signing_secret` defaults to the transport secret because in the normal
    # deployment there is one secret store. Setting it separately is what lets
    # approval be a different authority from transport — a node can then be
    # reachable by a coordinator that cannot, by itself, authorise an analysis.
    signature = sign(spec, signing_secret or secret)

    runs, failures, policies = fan_out(
        spec, endpoints, secret, project_id=project_id,
        spec_signature=signature, k_min=k_min,
        research_projects=research_projects, timeout=timeout,
        max_workers=max_workers, session=session)

    if not runs:
        raise NoSourcesAnswered(failures)

    return combine(spec, runs, coordinator_version=coordinator_version,
                   node_policies=policies, failures=failures,
                   snapshots={r.get("node_id", "?"): r.get("snapshot_at", "")
                              for r in runs if r.get("snapshot_at")})
