"""#825 — the demographic predicate is answered by ips, not by the CDRs.

`_resolve_members` used to resolve a cohort by fanning out
`GET /api/v1/fhir/Patient` across the federation. **No CDR has ever held a
Patient resource, and nothing on the platform writes one** — cdr's ingest
creates IngestRaw, FhirResource, ClinicalContext and an Observation row;
gateway and sim push observations; ips holds the registry and never propagates
it. So every cohort resolved to the empty set, on every predicate, since the
workspace was built.

Measured on production 2026-10-13: cdr1/cdr2/cdr3 each 0 rows in `patient`,
while cdr2 and cdr3 held 30 patients' worth of observations apiece and the
workspace's own "Available data" table said 30 patients.

Operator decision the same day: resolve against ips rather than copy
demographics into every CDR.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ips_client import IpsUnreachable


def _no_cdr_calls(method, url, params=None, json=None, headers=None, timeout=None):
    raise AssertionError(
        f"the cohort hit a CDR for {url} — the demographic predicate must go "
        f"to ips, and no CDR holds Patient resources to answer it")


class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._b = status, body
        self.text = str(body)

    def json(self):
        return self._b


def test_a_demographic_only_cohort_NEVER_touches_a_cdr(client, ips_patients):
    """The whole point. A predicate with no clinical criteria is answered
    entirely by ips; fanning out to a CDR would return nothing."""
    ips_patients({"a", "b", "c"})
    with patch("app.analyse.federation.requests.request",
               side_effect=_no_cdr_calls):
        r = client.post("/api/cohort", json={
            "cdr_ids": [], "demographics": {"age_min": 40, "age_max": 70}})
    assert r.status_code == 201, r.get_data(as_text=True)[:200]
    assert r.get_json()["n"] == 3


def test_the_cohort_is_what_ips_says(client, ips_patients):
    ips_patients({"only-one"})
    with patch("app.analyse.federation.requests.request",
               side_effect=_no_cdr_calls):
        r = client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}})
    assert r.get_json()["n"] == 1


def test_an_unreachable_ips_FAILS_CLOSED(client, app):
    """A cohort must never silently become 'everyone' because a sibling is
    down. Same direction as the spärr check, which treats an unanswerable
    verdict as blocked rather than clear."""
    from app.routes import researcher as r

    class _Broken:
        @staticmethod
        def analysis_filter(guids, purpose, projects=None):
            return {"allowed": list(guids), "excluded": []}

        @staticmethod
        def search_patients(**kw):
            raise IpsUnreachable("ips is down")

    with patch.object(r, "_ips_client", lambda: _Broken()):
        resp = client.post("/api/cohort",
                           json={"cdr_ids": [], "demographics": {"age_min": 40}})
    assert resp.status_code >= 400, (
        "an unreachable registry produced a cohort instead of an error")
    assert resp.status_code != 201


def test_the_demographic_predicate_is_PASSED_THROUGH_to_ips(client):
    """age/sex must reach ips, or every cohort is the whole registry wearing
    the analyst's label."""
    from app.routes import researcher as r
    seen = {}

    class _Spy:
        @staticmethod
        def analysis_filter(guids, purpose, projects=None):
            return {"allowed": list(guids), "excluded": []}

        @staticmethod
        def search_patients(**kw):
            seen.update(kw)
            return {"p1"}

    with patch.object(r, "_ips_client", lambda: _Spy()):
        client.post("/api/cohort", json={
            "cdr_ids": [],
            "demographics": {"age_min": 40, "age_max": 70, "sex": "female"}})
    assert seen.get("age_min") == 40
    assert seen.get("age_max") == 70
    assert seen.get("sex") == "female"


def test_rule_24_scope_travels_with_the_question(client):
    """A non-admin reader's organisations go to ips, so the REGISTRY decides
    what they may see rather than this service. The key must be present even
    when the reader has none — omitting it would mean 'no scoping'."""
    from app.routes import researcher as r
    seen = {}

    class _Spy:
        @staticmethod
        def analysis_filter(guids, purpose, projects=None):
            return {"allowed": list(guids), "excluded": []}

        @staticmethod
        def search_patients(**kw):
            seen.update(kw)
            return set()

    with patch.object(r, "_ips_client", lambda: _Spy()):
        client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}})
    assert "organisation_guids" in seen, (
        "no organisation scope was sent, so ips would answer for the whole "
        "registry regardless of who asked")


def test_the_client_posts_to_the_search_endpoint():
    """Through IpsClient itself, so a rename of the endpoint fails here rather
    than silently in production."""
    from app.services.ips_client import IpsClient
    c = IpsClient(token="t", base_url="http://ips.example")
    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp({"patient_guids": ["x", "y"]})

    with patch("app.services.ips_client.requests.post", side_effect=_post):
        got = c.search_patients(age_min=40, organisation_guids=["org-1"])
    assert captured["url"].endswith("/api/v1/patients/search")
    assert captured["json"] == {"age_min": 40, "organisation_guids": ["org-1"]}
    assert got == {"x", "y"}


def test_a_non_200_from_ips_raises_rather_than_returning_empty():
    """An empty set and a failure are different answers. Returning {} would
    read as 'nobody matched'."""
    from app.services.ips_client import IpsClient
    c = IpsClient(token="t", base_url="http://ips.example")
    with patch("app.services.ips_client.requests.post",
               return_value=_Resp({}, status=500)):
        with pytest.raises(IpsUnreachable):
            c.search_patients(age_min=40)


def test_the_registry_query_authenticates_with_ApiKey_not_a_user_bearer():
    """ips's require_auth reads ONLY `Authorization` and accepts exactly
    `Bearer` and `ApiKey`. It IGNORES `X-Service-Key`.

    The first deploy of this path sent the browsing user's SSO bearer plus
    `X-Service-Key`, and ips answered 401 — which this service correctly
    turned into a 503 on Define cohort. The cohort question is a SERVICE
    asking the registry, with the reader's Rule 24 scope carried explicitly in
    the body, so it authenticates as the service.
    """
    from app.services.ips_client import IpsClient
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen.update(headers or {})
        return _Resp({"patient_guids": []})

    c = IpsClient(token="a-user-token", service_key="svc",
                  api_key="the-ips-key", base_url="http://ips.example")
    with patch("app.services.ips_client.requests.post", side_effect=_post):
        c.search_patients(age_min=40)

    assert seen["Authorization"] == "ApiKey the-ips-key", (
        "the registry query did not authenticate as the service; ips ignores "
        "X-Service-Key, so a user bearer here is a 401 waiting to happen")


def test_without_an_api_key_it_still_sends_SOMETHING_rather_than_nothing():
    """A deployment that has not set IPS_API_KEY falls back to the existing
    header behaviour rather than sending no Authorization at all, which ips
    rejects as an unsupported scheme."""
    from app.services.ips_client import IpsClient
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen.update(headers or {})
        return _Resp({"patient_guids": []})

    c = IpsClient(token="a-user-token", base_url="http://ips.example")
    with patch("app.services.ips_client.requests.post", side_effect=_post):
        c.search_patients()
    assert seen.get("Authorization") == "Bearer a-user-token"


def test_the_CONSENT_filter_also_authenticates_as_the_service():
    """Same defect, same file, one method apart — and found only because the
    registry fix made the 503 move rather than disappear.

    `analysis_filter` sent the user's bearer plus `X-Service-Key`, which ips
    ignores, so it answered 401 and every cohort failed closed with a 503. The
    research-consent gate has therefore never returned a verdict from
    analyse — safe direction, non-functional, exactly like the spärr check in
    #717.
    """
    from app.services.ips_client import IpsClient
    seen = {}

    def _post(url, json=None, headers=None, timeout=None):
        seen.update(headers or {})
        return _Resp({"allowed": ["p1"], "excluded": []})

    c = IpsClient(token="a-user-token", service_key="svc",
                  api_key="the-ips-key", base_url="http://ips.example")
    with patch("app.services.ips_client.requests.post", side_effect=_post):
        c.analysis_filter(["p1"], "research", [])
    assert seen["Authorization"] == "ApiKey the-ips-key"
