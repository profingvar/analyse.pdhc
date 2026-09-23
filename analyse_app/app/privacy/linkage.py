"""keyed_hash linkage, behind a feature flag (#660).

The third linkage mode, deliberately deferred and deliberately gated.

Each node computes ``HMAC(linkage_key, personnummer)`` LOCALLY. Two nodes
holding the same patient produce the same token, so the coordinator can
DEDUPLICATE A COUNT without either node revealing who its patients are.

THE LINE THAT MATTERS, and the reason this is gated rather than merely
configurable:

    Deduplicated COUNTING is safe. Two tokens matching tells the coordinator
    that one patient appears twice — a fact about arithmetic, not about a
    person.

    JOINING per-patient VARIABLES across nodes is not. That requires
    patient-level data to leave a node, which needs trusted mode AND a
    documented legal basis. This module refuses to do it, and the refusal is
    in code rather than in a comment.

A constraint discovered in AN-0 (gap G9) and worth repeating here: **the CDR
holds no personnummer**. The platform is GUID-keyed throughout, so this mode
cannot be served by a CDR alone — the token would have to come from ips.pdhc,
which owns patient identity. Until that path exists, this mode is
unimplementable in production however the flag is set.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Iterable


class LinkageDisabled(RuntimeError):
    """keyed_hash was requested without the feature flag."""


class LinkageNotPermitted(RuntimeError):
    """A join was attempted that this mode does not allow."""


def enabled(env: dict | None = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get("ANALYSE_ENABLE_KEYED_HASH", "")).lower() in (
        "1", "true", "yes")


@dataclass(frozen=True)
class LinkageKey:
    """Held by the nodes or a trusted third party — never by the coordinator.

    If the coordinator held it, it could compute tokens for any identity it
    cared to guess and test them against what the nodes returned. That turns
    a deduplication token into an oracle.
    """
    key: bytes

    def __post_init__(self):
        if len(self.key) < 32:
            raise ValueError("linkage key must be at least 32 bytes")

    def __repr__(self) -> str:      # pragma: no cover
        return "<LinkageKey redacted>"

    __str__ = __repr__


def token(identifier: str, key: LinkageKey, *, env: dict | None = None) -> str:
    """The linkage token for one patient, computed ON THE NODE."""
    if not enabled(env):
        raise LinkageDisabled(
            "keyed_hash linkage is behind ANALYSE_ENABLE_KEYED_HASH and is "
            "off; see docs/analyse/decisions/0010-keyed-hash-linkage.md")
    if not identifier:
        raise ValueError("identifier is required")
    return hmac.new(key.key, identifier.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:24]


def deduplicated_count(token_sets: Iterable[Iterable[str]]) -> int:
    """The only operation this mode permits: how many DISTINCT patients the
    sources hold between them.

    Takes token sets and returns a number. It deliberately does not return
    the union, the overlap, or which tokens were shared — each of those is a
    step toward a join, and a coordinator holding the overlap could ask a
    node about those specific patients."""
    seen: set[str] = set()
    for s in token_sets:
        seen.update(s)
    return len(seen)


def join_across_nodes(*_args, **_kwargs):
    """Refused. See the module docstring.

    Present as a function so the attempt fails loudly and names the reason,
    rather than someone building the join out of ``deduplicated_count`` and a
    set intersection and believing it was permitted because nothing stopped
    them."""
    raise LinkageNotPermitted(
        "joining per-patient variables across nodes means patient-level data "
        "leaves a node. That requires trusted mode and a documented legal "
        "basis, and is not what keyed_hash linkage authorises.")
