"""Where a node gets its cohort (#684 / AN-12).

**No patient identifier crosses the coordinator/node boundary, in either
direction.** That is the reason this seam exists at all. The coordinator sends
a question; each node decides which of *its* patients the question is about.
If the coordinator sent a list of patient guids it would be holding
identifiers for every source, which is the arrangement the whole design exists
to avoid — and `app/testing/scanner.py` would flag them in its own output.

## What is built here

A seam, and the implementation the in-process harness has always used. The
node resolves its cohort locally, through a `CohortSource`.

## What is NOT built, and must not be mistaken for built

Deriving a cohort from `spec.cohort.include` against a live CDR. Two separate
things are missing for that, neither of them transport:

1. **The node runner never applies `spec.cohort` at all.** `run_spec()` takes
   an already-resolved list of patient guids; the spec's inclusion criteria
   are not read by anything on the node path. That predates this ticket.
2. **The CDR exposes no patient-listing or criterion-search endpoint** that a
   node could use to answer "which of your patients match this". The node's
   read client speaks to `/api/v1/observations/search`, which needs the
   patient guids it is meant to produce.

`ConfiguredCohortSource` is therefore what a real deployment gets today: the
operator pins the cohort per node. That is a real limitation, written down
rather than papered over with an invented endpoint.
"""
from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


class CohortError(RuntimeError):
    """The node could not establish which patients this question is about.

    Raised, never returned empty. An empty cohort and an unresolvable one look
    identical downstream — both produce no partials — but they mean opposite
    things: one is an honest "no patients here", the other is a broken node
    quietly contributing nothing to a pooled figure.
    """


@runtime_checkable
class CohortSource(Protocol):
    def resolve(self, spec) -> list[str]:
        """Patient guids at THIS node that the spec is about."""
        ...


class StaticCohortSource:
    """A fixed cohort. Used by the synthetic harness and by tests."""

    def __init__(self, patient_guids: Iterable[str]):
        self._guids = sorted({g for g in patient_guids if g})

    def resolve(self, spec) -> list[str]:  # noqa: ARG002 - fixed by construction
        return list(self._guids)


class ConfiguredCohortSource:
    """The cohort an operator pinned for this node, from its config.

    Deliberately explicit. A node that cannot say who its cohort is refuses
    rather than answering about nobody.
    """

    def __init__(self, config, key: str = "ANALYSE_NODE_COHORT"):
        self._config = config
        self._key = key

    def resolve(self, spec) -> list[str]:  # noqa: ARG002
        raw = self._config.get(self._key)
        if raw is None:
            raise CohortError(
                f"this node has no cohort configured ({self._key}) and cannot "
                f"derive one from the spec: resolving spec.cohort against a "
                f"CDR is not implemented — see the node cohort-resolution "
                f"ticket")
        if isinstance(raw, str):
            raw = [chunk.strip() for chunk in raw.split(",")]
        guids = sorted({g for g in raw if g})
        if not guids:
            raise CohortError(
                f"{self._key} is set but empty — refusing to report a figure "
                f"over nobody as though it were an answer")
        return guids
