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

from concurrent.futures import ThreadPoolExecutor

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

    def excluded_by_spärr(self, patient_guids, ips_base_url: str, *,
                          source_clinic_id: str | None = None,
                          ips_api_key: str | None = None) -> set[str]:
        """Patients whose data is blocked, excluded ON THE NODE before any
        computation — an aggregate computed over a blocked patient has already
        used their data even if the number is later thrown away.

        Fails CLOSED: any failure to establish a verdict treats every patient
        as blocked rather than none.

        #717. This used to POST to ``/api/v1/blocks/check-bulk``, which has
        never existed — ips has no bulk endpoint at all. Every call 404'd, so
        the gate failed closed on every run and a node could never return a
        single row. The real predicate is per patient and per SOURCE:

            GET /api/v1/patients/<guid>/blocks/check?source_clinic_id=<org>
            Authorization: ApiKey <key>

        Both details matter and both were wrong before: ips's ``require_auth``
        reads ONLY the ``Authorization`` header and silently ignores
        ``X-API-Key``, and spärr is a question about a source, not about a
        patient in isolation. request.pdhc made the identical pair of mistakes
        (see its ips_client docstring) and its filter silently failed OPEN —
        no ServiceRequest was ever hidden. This one failed closed, which is
        the safe direction but equally non-functional.
        """
        guids = sorted({g for g in patient_guids if g})
        if not guids:
            return set()
        if not ips_base_url:
            raise ConsentUnavailable(
                "IPS_BASE_URL is not configured, so spärr cannot be checked "
                "and every patient is treated as blocked")
        if not source_clinic_id:
            raise ConsentUnavailable(
                "this node's policy has no source_clinic_id, so ips cannot be "
                "asked whether this source is blocked. Set it in the node's "
                "policy file; until then every patient is treated as blocked")
        if not ips_api_key:
            raise ConsentUnavailable(
                "IPS_API_KEY is not configured, so ips will refuse the spärr "
                "check and every patient is treated as blocked")

        base = ips_base_url.rstrip("/")
        headers = {"Accept": "application/json",
                   "Authorization": f"ApiKey {ips_api_key}"}

        def _one(guid: str) -> tuple[str, bool]:
            resp = requests.get(
                f"{base}/api/v1/patients/{guid}/blocks/check",
                params={"source_clinic_id": source_clinic_id},
                headers=headers, timeout=self.timeout)
            if resp.status_code == 404:
                # Unknown to ips is genuinely "no blocks recorded", not an
                # outage — the same reading request.pdhc takes.
                return guid, False
            if resp.status_code != 200:
                raise ReadError(
                    f"blocks/check for {guid[:8]} returned {resp.status_code}")
            return guid, bool((resp.json() or {}).get("is_blocked"))

        try:
            blocked: set[str] = set()
            # A cohort is many patients and ips answers one at a time, so this
            # is the one place a node fans out. Bounded, because an unbounded
            # pool against a sibling is a denial of service with extra steps.
            with ThreadPoolExecutor(max_workers=min(8, len(guids))) as pool:
                for guid, is_blocked in pool.map(_one, guids):
                    if is_blocked:
                        blocked.add(guid)
            return blocked
        except Exception as e:
            # Deliberately broad. Any failure to establish the verdict must
            # surface as ONE exception type, so a caller handling
            # ConsentUnavailable does not also need a bare except to stay safe.
            raise ConsentUnavailable(
                f"spärr could not be checked ({e}) — every patient is "
                f"treated as blocked rather than none") from e
