"""The node's read client (#648, repointed by #823).

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

## #823: this client used to call an endpoint that does not exist

``read_observations`` POSTed to ``/api/v1/observations/search``. **No CDR
serves it** — 404 on cdr2 and cdr3, measured 2026-10-13, and absent from
cdr.pdhc's tree as well as from the deployed url_map. No node is deployed
(``ANALYSE_ROLE=coordinator``), so the read path had never executed against a
real CDR, and every test of it mocked the reader: 579 tests passed over a
request that 404s in production.

The bulk read a CDR actually serves is ``GET /api/v1/fhir/Observation``, and
it already answers everything the node needs. Four details of it are load
bearing, each measured rather than assumed:

* **``patient`` is honoured and exact, but ONE GUID AT A TIME.** cdr.pdhc's
  tree accepts a comma-separated allowlist (#701,
  ``fhir_read._requested_patients``, 500 per request) and **that code is
  deployed nowhere**: measured 2026-10-14 against cdr1 and cdr2,
  ``patient=<a>,<b>`` returns ZERO rows on both, because the deployed
  version matches the whole comma string as one guid (#826). A single guid
  returns only that patient's rows on both, verified by reading the subjects
  back rather than counting. So this client fans out one request per patient —
  the same shape ``excluded_by_spärr`` uses for the same reason. Narrowing
  server-side is not an optimisation here: the node excludes spärr-blocked
  patients BEFORE it reads, and that ordering is the security argument, so
  the CDR must never be asked for rows it would then hand back.
* **``code`` is matched against the stored canonical, and the two spellings
  are not interchangeable.** ``code=<system>|<guid>`` becomes an indexed
  equality against ``urn:pdhc:concept/<guid>``; a bare ``code=<guid>``
  becomes ``LIKE '%/<guid>'``, which also matches but forces a sequential
  scan; and ``code=urn:pdhc:concept/<guid>`` — the obvious thing to send —
  matches NOTHING, because it is read as a bare code and becomes
  ``LIKE '%/urn:pdhc:concept/<guid>'``. That asymmetry cost a wrong
  conclusion during #823 and is why this module sends the piped form.
* **An unknown query parameter is silently ignored** — ``totally-made-up=xyz``
  returns a full unfiltered page, measured on both CDRs. A typo therefore
  narrows nothing and reads everything, with no error, and *a count cannot
  tell you*: with ``_count=50`` an honoured filter and an ignored one both
  return 50 rows. That is how the comma finding above was nearly missed. So
  every narrowing this client asks for is RE-CHECKED on the way back (see
  ``_rows``): a row for a patient other than the one requested means the
  filter did not happen, and that is raised rather than filtered away.
  Reading rows for a patient the node excluded is the failure this ordering
  exists to prevent, and it must not be discoverable only by its absence.
* **``Bundle.total`` is the PAGE SIZE, not the match count** (``_count=500``
  yields ``total=500``), ``_summary=count`` returns 100 full resources
  instead of a count, and there is no offset parameter at all. So there is no
  way to page and no trustworthy way to ask how many matched. This client
  therefore asks for cdr's ceiling and treats a full page as a TRUNCATED
  read — ``ReadError``, not a silently short answer. A statistic computed
  over an unknown fraction of a cohort is worse than no statistic.

What this endpoint cannot give: ``meta.author_org``. cdr #665 stores
``author_org_guid`` on ``clinical_context``, which is not part of the
Observation's ``raw_json``, so it is not in the Bundle. The DECLARED
``performer[].identifier.value`` is mapped in its place and is exactly that —
declared by the submitter, not attested — so ``completeness``'s
``by_author_org`` must be read as "who the submitter said", and a row without
a performer stays absent rather than being filled in.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from dataclasses import dataclass
from typing import Any, Iterable

import requests

DEFAULT_TIMEOUT = 30.0

#: How many patients this client asks about at once. ONE, because the
#: deployed CDRs honour a single `patient` guid and return zero rows for a
#: comma-separated list — see the module docstring, and #826, which is the
#: deployment gap itself. When `_requested_patients` is actually deployed
#: this becomes a batch size and the fan-out collapses into one request per
#: 500 patients; until then a larger value reads nothing, silently. Do not
#: raise it on the evidence of cdr.pdhc's source — that is the mistake #826
#: documents. Verify by reading the returned SUBJECTS back, never by
#: counting rows.
PATIENTS_PER_REQUEST = 1

#: Concurrent reads against one CDR. Bounded, because an unbounded pool
#: against a sibling is a denial of service with extra steps.
MAX_CONCURRENCY = 8

#: cdr's `_count` ceiling (`min(int(request.args.get("_count", 100)), 30000)`).
#: Asked for in full because there is no offset parameter to page with.
MAX_COUNT = 30000

#: The system half of the piped `code` form. cdr stores
#: `urn:pdhc:concept/<guid>` and `_filter_by_code` rebuilds exactly that from
#: `<system>|<code>`, so this is an indexed equality rather than a LIKE scan.
#: Measured 2026-10-13 across cdr1/cdr2/cdr3 (1,942 + 19,490 + 19,509 rows
#: with a canonical): 100% in this spelling, zero in the legacy
#: `https://plan.pdhc.se/api/v1/concepts/<guid>` form, so equality loses
#: nothing. If a legacy row ever appears, the bare-guid LIKE form matches
#: both and is the fallback — at the cost of the sequential scan.
CONCEPT_SYSTEM = "urn:pdhc:concept"

#: Systems whose `code` is a PDHC concept guid, for reading the concept back
#: out of a returned resource. Both spellings are accepted on the way in
#: (gateway dual-emits during the #294 alias window).
_CONCEPT_SYSTEMS = (CONCEPT_SYSTEM, "https://plan.pdhc.se/api/v1/concepts")


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
        """Fetch observations for a cohort, consent-filtered by the CDR.

        One request per (patient batch x concept). The concept loop is a loop
        and not one request because cdr's `code` is single-valued: a repeated
        `code=` is read as the last one, which would narrow to one concept
        while reporting on several. Asking separately costs requests and
        cannot be wrong about which rows belong to which concept.

        Omitting `concepts` reads every concept for the cohort, which is a
        wider read than a spec needs. Callers that know their concepts should
        pass them.
        """
        guids = sorted({g for g in patient_guids if g})
        if not guids:
            return []
        codes: list[str | None] = sorted({c for c in concepts if c}) or [None]

        headers = self._headers(purpose=purpose,
                                research_projects=research_projects)
        work = [(guids[i:i + PATIENTS_PER_REQUEST], code)
                for i in range(0, len(guids), PATIENTS_PER_REQUEST)
                for code in codes]

        def _one(item):
            batch, code = item
            return self._rows(self._search(batch, code, headers),
                              allowed=set(batch))

        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(
                max_workers=min(MAX_CONCURRENCY, len(work))) as pool:
            # `pool.map` re-raises the first exception, which is what should
            # happen: a cohort read that partly failed must not be reported
            # as a complete one.
            for chunk in pool.map(_one, work):
                rows.extend(chunk)
        return rows

    def _search(self, patient_guids: list[str], code: str | None,
                headers: dict[str, str]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "patient": ",".join(patient_guids),
            "_count": MAX_COUNT,
        }
        if code:
            params["code"] = f"{CONCEPT_SYSTEM}|{code}"
        try:
            r = requests.get(
                f"{self.base_url.rstrip('/')}/api/v1/fhir/Observation",
                params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise ReadError(f"read failed: {e}") from e

        if r.status_code == 503:
            # cdr fails closed when ips cannot answer. So does the node: a
            # missing consent verdict is not a reason to proceed.
            raise ConsentUnavailable(
                "the consent filter is unavailable — this read fails closed "
                "rather than returning unfiltered data")
        if r.status_code == 400:
            # cdr 400s on a malformed search parameter as well as on a refused
            # purpose — a dropped predicate returns more rows than were asked
            # for, so it refuses rather than widening. Both are reportable.
            raise ReadError(f"the CDR refused this read: {r.text[:200]}")
        if r.status_code != 200:
            raise ReadError(f"read returned {r.status_code}")

        body = r.json() or {}
        entries = body.get("entry") or []
        if len(entries) >= MAX_COUNT:
            # cdr offers no offset parameter and `Bundle.total` reports the
            # page size, so a full page is indistinguishable from a complete
            # answer. Refusing is the only honest option: a figure computed
            # over an unknown fraction of a cohort reads as a real figure.
            raise ReadError(
                f"this read filled cdr's maximum page ({MAX_COUNT} rows) and "
                f"the endpoint has no way to fetch the rest — narrow the "
                f"concepts or batch the cohort rather than reporting over a "
                f"truncated read")
        return body

    def _rows(self, bundle: dict[str, Any],
              *, allowed: set[str]) -> list[dict[str, Any]]:
        """FHIR Bundle entries → the flat rows the node projects from.

        The allowlist re-check is the point of this being a method rather than
        a comprehension. cdr ignores an unknown query parameter silently, so
        "the server narrowed by patient" is an assumption until the rows are
        inspected — and if it did not, the node is holding rows for patients
        it excluded, which is the one thing step 3 of the pipeline exists to
        prevent. Raising names the cause; dropping them would leave a node
        that looks correct and reads too widely.
        """
        out: list[dict[str, Any]] = []
        for entry in bundle.get("entry") or []:
            res = entry.get("resource") or {}
            guid = _subject_guid(res)
            if not guid:
                continue
            if guid not in allowed:
                raise ReadError(
                    f"the CDR returned a row for a patient that was not "
                    f"asked about ({guid[:8]}…) — the `patient` filter was "
                    f"not applied, so this read is wider than the cohort it "
                    f"was asked for")
            value, unit = _value_of(res)
            row: dict[str, Any] = {
                "patient_guid": guid,
                "concept": _concept_of(res),
                "value": value,
                "unit": unit,
                # Raw calendar time. Coarsened to a day_offset before
                # projection — see app/privacy/coarsen.py (#824). It must not
                # reach an allowlist that was written to keep dates out.
                "effective_at": _effective_at(res),
            }
            org = _performer_org(res)
            if org:
                row["meta"] = {"author_org": org}
            out.append(row)
        return out

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


# ---------------------------------------------------------------------------
# FHIR Observation -> flat row
#
# Deliberately total functions over a Mapping: a missing field is None, never
# an exception. Absence is a fact about the observation (AN-4 reports
# missingness) and a reader that raised on an incomplete resource would make
# one odd row lose a whole cohort's read.
# ---------------------------------------------------------------------------

def _subject_guid(res: dict) -> str | None:
    """`subject.reference` is "Patient/<guid>"; the identifier form is the
    fallback for a producer that sent one instead."""
    subject = res.get("subject") or {}
    ref = subject.get("reference") or ""
    if ref:
        return ref.rsplit("/", 1)[-1] or None
    return (subject.get("identifier") or {}).get("value") or None


def _concept_of(res: dict) -> str | None:
    """The BARE concept guid, which is what the spec's `from` names and what
    `node/frame.py` matches on — not the canonical URI it is stored under.

    A PDHC coding is preferred over any other; LOINC and friends are real
    external code systems, and picking whichever the producer listed first
    would make the concept a row's `concept` depend on array order.
    """
    codings = (res.get("code") or {}).get("coding") or []
    for coding in codings:
        system = (coding.get("system") or "").rstrip("/")
        if system in _CONCEPT_SYSTEMS and coding.get("code"):
            return coding["code"]
    for coding in codings:
        if coding.get("code"):
            return coding["code"]
    return None


def _value_of(res: dict) -> tuple[Any, Any]:
    """(value, unit). `valueQuantity` carries both; the other value[x] forms
    carry no unit, and `frame._numeric` coerces a numeric string."""
    vq = res.get("valueQuantity")
    if isinstance(vq, dict):
        return vq.get("value"), vq.get("unit") or vq.get("code")
    for key in ("valueInteger", "valueString", "valueBoolean"):
        if key in res:
            return res[key], None
    cc = res.get("valueCodeableConcept")
    if isinstance(cc, dict):
        coding = (cc.get("coding") or [{}])[0]
        return coding.get("code") or cc.get("text"), None
    return None, None


def _effective_at(res: dict) -> str | None:
    """A period's START, not its end: an observation is placed at the moment
    it began, and `day_offset` is measured from an index event that is itself
    a start."""
    when = res.get("effectiveDateTime")
    if when:
        return when
    period = res.get("effectivePeriod") or {}
    if period.get("start"):
        return period["start"]
    # `issued` is when the CDR received it, not when it happened. Used only as
    # a last resort and worth knowing when a day_offset looks shifted.
    return res.get("issued") or None


def _performer_org(res: dict) -> str | None:
    """The DECLARED performer org. See the module docstring: cdr's attested
    `author_org_guid` lives on clinical_context and is not in this resource,
    so this is the submitter's claim and nothing stronger."""
    for performer in res.get("performer") or []:
        if not isinstance(performer, dict):
            continue
        ident = (performer.get("identifier") or {}).get("value")
        if ident:
            return ident
        ref = performer.get("reference") or ""
        if ref:
            return ref.rsplit("/", 1)[-1] or None
    return None
