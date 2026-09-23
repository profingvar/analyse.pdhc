"""The node's read client (#648).

The node reads through the PLATFORM READ SERVICE and never the store. That is
a rule from the brief and it is also the only way consent and spärr get
applied: the gates live on the read path, so a node that went around them
would be fast, correct-looking and unlawful.

Two headers carry what cdr needs, both shipped in cdr #664:

    X-Access-Purpose          the spec's purpose, from the platform enum
    X-Research-Project-Guids  required when the purpose is research

Before #664, a service-key caller passed the consent gate untouched, because
"a machine identity has no role to derive a purpose from". A node has one —
it is in the spec — so it declares it and is filtered like any other reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import requests

DEFAULT_TIMEOUT = 30.0


class ReadError(RuntimeError):
    pass


class ConsentUnavailable(ReadError):
    """The consent filter could not answer. Reads fail CLOSED."""


@dataclass
class NodeReader:
    base_url: str
    service_key: str
    source_service: str = "analyse.pdhc"
    timeout: float = DEFAULT_TIMEOUT

    def _headers(self, *, purpose: str,
                 research_projects: Iterable[str] = ()) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "X-Service-Key": self.service_key,
            "X-Source-Service": self.source_service,
            # cdr #664: declare the purpose so the consent join runs. Without
            # this header the CDR passes machine callers through unfiltered,
            # which for an analysis node would mean reading data the patient
            # objected to.
            "X-Access-Purpose": purpose,
        }
        projects = [p for p in research_projects if p]
        if projects:
            h["X-Research-Project-Guids"] = ",".join(projects)
        return h

    def read_observations(self, *, purpose: str, patient_guids: Iterable[str],
                          concepts: Iterable[str] = (),
                          research_projects: Iterable[str] = (),
                          ) -> list[dict[str, Any]]:
        """Fetch observations for a cohort, consent-filtered by the CDR."""
        payload = {
            "patient_guids": sorted(set(patient_guids)),
            "concepts": sorted(set(concepts)),
        }
        try:
            r = requests.post(
                f"{self.base_url.rstrip('/')}/api/v1/observations/search",
                json=payload,
                headers=self._headers(purpose=purpose,
                                      research_projects=research_projects),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise ReadError(f"read failed: {e}") from e

        if r.status_code == 503:
            # cdr fails closed when ips cannot answer. So does the node: a
            # missing consent verdict is not a reason to proceed.
            raise ConsentUnavailable(
                "the consent filter is unavailable — this read fails closed "
                "rather than returning unfiltered data")
        if r.status_code == 400:
            raise ReadError(f"the CDR refused the declared purpose: {r.text[:200]}")
        if r.status_code != 200:
            raise ReadError(f"read returned {r.status_code}")
        body = r.json() or {}
        return list(body.get("items") or body.get("observations") or [])

    def excluded_by_spärr(self, patient_guids: Iterable[str],
                          ips_base_url: str) -> set[str]:
        """Patients whose data is blocked. Excluded ON THE NODE, before any
        computation — the brief is explicit, and an aggregate computed over a
        blocked patient has already used their data even if the number is
        later thrown away.

        Fails CLOSED: if ips cannot answer, every patient is treated as
        blocked rather than none.
        """
        guids = sorted(set(patient_guids))
        if not guids:
            return set()
        try:
            r = requests.post(
                f"{ips_base_url.rstrip('/')}/api/v1/blocks/check-bulk",
                json={"patient_guids": guids},
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                raise ReadError(f"blocks/check-bulk returned {r.status_code}")
            body = r.json() or {}
            return set(body.get("blocked") or [])
        except Exception as e:
            # Deliberately broad. Any failure to establish the spärr verdict
            # must surface as ONE exception type, so a caller handling
            # ConsentUnavailable does not also need a bare except to stay
            # safe. An unexpected error here is still a missing verdict.
            raise ConsentUnavailable(
                f"spärr could not be checked ({e}) — every patient is "
                f"treated as blocked rather than none") from e
