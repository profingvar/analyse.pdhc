"""Per-project pseudonyms (#645).

Inside a node the patient key becomes ``HMAC-SHA256(project_key,
patient_guid)``, truncated. Each analysis project has its OWN key, so
pseudonyms from two projects cannot be joined — which is the point: without
per-project keys, a pseudonym is a stable identifier across every analysis
anyone ever ran, and linking them re-creates the patient.

HMAC, not a plain hash. A patient GUID is drawn from a small, enumerable
space; an unkeyed digest of it is reversible by brute force in seconds. The
key is what makes this one-way in practice and not just in principle.

There is deliberately no inverse function here, and none should be added.
"""
from __future__ import annotations

import hashlib
import hmac
import os

#: 16 hex characters = 64 bits. By the birthday bound a collision becomes
#: likely around 2^32 patients, which is far past any cohort this platform
#: will hold, while staying short enough to read in a table.
PSEUDONYM_HEX_LEN = 16


class ProjectKey:
    """An HMAC key that refuses to render itself.

    A key reaches a log through the dullest possible route: someone formats
    a config object, or an exception carries locals. ``__repr__`` and
    ``__str__`` are overridden so that the obvious accidents produce
    ``<ProjectKey redacted>`` rather than key material. It is not a
    substitute for keeping keys out of logs, only a floor under it.
    """

    __slots__ = ("_key", "project_id")

    def __init__(self, project_id: str, key: bytes):
        if not project_id:
            raise ValueError("project_id is required")
        if not isinstance(key, (bytes, bytearray)) or len(key) < 32:
            raise ValueError(
                "project key must be at least 32 bytes of key material")
        self.project_id = project_id
        self._key = bytes(key)

    def __repr__(self) -> str:      # pragma: no cover - trivial
        return f"<ProjectKey {self.project_id} redacted>"

    __str__ = __repr__

    def __eq__(self, other) -> bool:
        return (isinstance(other, ProjectKey)
                and hmac.compare_digest(self._key, other._key)
                and self.project_id == other.project_id)

    def __hash__(self) -> int:
        # Hash the project id only — never the key material.
        return hash(self.project_id)

    @classmethod
    def from_env(cls, project_id: str, *, env: dict | None = None) -> "ProjectKey":
        """Load from the platform's secret store, via the environment.

        Never from the spec, a request, or a file in the repo: the spec is
        signed and travels between services, and anything in it is visible to
        every node it reaches.
        """
        source = env if env is not None else os.environ
        var = f"ANALYSE_PROJECT_KEY_{project_id.upper().replace('-', '_')}"
        raw = source.get(var)
        if not raw:
            raise KeyError(
                f"no project key configured for '{project_id}' "
                f"(expected {var} in the secret store)")
        return cls(project_id, raw.encode("utf-8"))


def pseudonymise(patient_guid: str, key: ProjectKey) -> str:
    """The patient's identifier INSIDE this project, and nowhere else."""
    if not patient_guid:
        raise ValueError("patient_guid is required")
    mac = hmac.new(key._key, patient_guid.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:PSEUDONYM_HEX_LEN]


def pseudonymise_all(patient_guids, key: ProjectKey) -> dict[str, str]:
    """guid -> pid for a batch. The mapping NEVER leaves the node."""
    return {g: pseudonymise(g, key) for g in patient_guids}
