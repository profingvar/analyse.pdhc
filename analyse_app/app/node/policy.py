"""Node policy — owned by the organisation behind the CDR (#648).

The coordinator CANNOT override any of this. That is the point: a node runs
beside one CDR, and the organisation responsible for those rows decides what
may be asked of them. A policy the coordinator could relax would be a
suggestion, not a policy.

Every field here answers a question the brief poses, and refusing is always
the safe direction: an unknown purpose is refused, an unparseable policy
fails to load rather than defaulting to permissive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from app.privacy.disclosure import DEFAULT_K_MIN, DisclosurePolicy
from app.spec import Purpose

#: The analyses a node may be asked to run. Names match the spec's analysis
#: `type` values.
ALL_ANALYSES = frozenset({
    "describe", "histogram", "frequency", "correlation",
    "compare_groups", "over_time", "completeness",
})


class PolicyError(ValueError):
    """A policy could not be loaded, or refuses this request."""


@dataclass(frozen=True)
class NodePolicy:
    """One CDR's terms of engagement."""

    node_id: str
    cdr_base_url: str

    #: Purposes this organisation permits. Empty means none — a node with no
    #: stated purposes answers nothing, which is the correct posture for a
    #: policy someone forgot to fill in.
    permitted_purposes: frozenset[str] = frozenset()

    permitted_analyses: frozenset[str] = ALL_ANALYSES

    #: This organisation's floor. A coordinator may ask for stricter, never
    #: looser (DisclosurePolicy enforces the direction).
    k_min: int = DEFAULT_K_MIN

    #: May this node's aggregates be pooled with other sources, or must they
    #: be reported per-source only? Some agreements permit contribution to a
    #: joint figure; others permit only a figure attributable to this
    #: organisation.
    may_pool: bool = True

    #: May rows AUTHORED by other organisations, but stored in this CDR, be
    #: used? Answerable only since cdr #665 added author_org_guid; before
    #: that the question had no field to consult.
    may_use_other_orgs_rows: bool = False

    #: synthetic | live. Live requires a deliberate change and is logged.
    data_mode: str = "synthetic"

    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path_or_text: str, *, is_text: bool = False) -> "NodePolicy":
        text = path_or_text if is_text else open(path_or_text, encoding="utf-8").read()
        try:
            blob = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise PolicyError(f"policy is not valid YAML: {e}") from e
        if not isinstance(blob, dict):
            raise PolicyError("policy must be a mapping")

        known = {"node_id", "cdr_base_url", "permitted_purposes",
                 "permitted_analyses", "k_min", "may_pool",
                 "may_use_other_orgs_rows", "data_mode"}
        unknown = set(blob) - known
        if unknown:
            # Loud, not ignored: a misspelled key in a policy file is a
            # control the organisation believes it has and does not.
            raise PolicyError(
                f"unknown policy keys: {', '.join(sorted(unknown))}")

        for required in ("node_id", "cdr_base_url"):
            if not blob.get(required):
                raise PolicyError(f"policy needs '{required}'")

        purposes = frozenset(blob.get("permitted_purposes") or [])
        bad = purposes - {p.value for p in Purpose}
        if bad:
            raise PolicyError(
                f"unknown purposes in policy: {', '.join(sorted(bad))}")

        analyses = frozenset(blob.get("permitted_analyses") or ALL_ANALYSES)
        bad = analyses - ALL_ANALYSES
        if bad:
            raise PolicyError(
                f"unknown analysis types in policy: {', '.join(sorted(bad))}")

        mode = blob.get("data_mode", "synthetic")
        if mode not in ("synthetic", "live"):
            raise PolicyError("data_mode must be 'synthetic' or 'live'")

        return cls(
            node_id=blob["node_id"], cdr_base_url=blob["cdr_base_url"],
            permitted_purposes=purposes, permitted_analyses=analyses,
            k_min=int(blob.get("k_min", DEFAULT_K_MIN)),
            may_pool=bool(blob.get("may_pool", True)),
            may_use_other_orgs_rows=bool(
                blob.get("may_use_other_orgs_rows", False)),
            data_mode=mode, raw=blob,
        )

    # ── enforcement ───────────────────────────────────────────────────

    def check(self, spec) -> None:
        """Refuse a spec this organisation does not permit. Raises."""
        if spec.purpose.value not in self.permitted_purposes:
            raise PolicyError(
                f"node '{self.node_id}' does not permit purpose "
                f"'{spec.purpose.value}'")
        asked = {a.type for a in spec.analyses}
        refused = asked - self.permitted_analyses
        if refused:
            raise PolicyError(
                f"node '{self.node_id}' does not permit analysis types: "
                f"{', '.join(sorted(refused))}")

    def disclosure(self, requested: DisclosurePolicy | None = None
                   ) -> DisclosurePolicy:
        """This node's disclosure policy, at least as strict as its own floor.

        A coordinator may ask for a higher k_min and get it. It may ask for a
        lower one and get this node's, silently — the request is not an error,
        it simply has no power here.
        """
        mine = DisclosurePolicy(k_min=self.k_min, floor=self.k_min)
        return mine if requested is None else mine.stricter_of(requested)
