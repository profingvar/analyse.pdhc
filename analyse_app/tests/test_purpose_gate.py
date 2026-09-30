"""#700 — the federated patient-data endpoints fail closed on read purpose.

Both CDR consent gates (#664 on cdr1-5, #699 on cdr_6) are opt-in: a caller
that declares nothing passes through untouched. Nothing on these endpoints
declared anything, so a named patient's rows were fanned out of every CDR with
analyse's service key and neither side applied the analysis-consent join.

The endpoints now require the caller to declare. That is deliberately not the
same as analyse declaring on the caller's behalf — the correct basis is a
property of the caller, and the wrong guess is not neutral: `research` applies
an EHDS opt-out to what may be a care-delivery read.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_app
from app.analyse.purpose import (
    DECLARABLE_SERVICE_PURPOSES, PURPOSE_HEADER, RESEARCH_PROJECTS_HEADER,
)

PATIENT_ENDPOINTS = [
    "/api/v1/canonical/health_observations?patient_guid=p1",
    "/api/v1/openehr/composition?patient=p1",
    "/api/v1/openehr/ehr/p1/compositions",
]

AUTH = {"X-Source-Service": "gateway.pdhc", "X-Service-Key": "right"}


class _FakeEndpoint:
    cdr_id = "cdr1"
    base_url = "http://cdr1"


class _FakeRegistry:
    """One reachable CDR, so the routes get past the empty-registry
    short-circuit and actually reach the fan-out."""
    all = [_FakeEndpoint()]

    @classmethod
    def from_config(cls, _cfg):
        return cls()


@pytest.fixture
def svc(monkeypatch):
    """A real service-key caller — the auth path is exercised, not patched."""
    import app.analyse.canonical as c
    import app.analyse.openehr as o
    import app.analyse.stats as st
    for mod in (c, o, st):
        monkeypatch.setattr(mod, "CdrRegistry", _FakeRegistry, raising=True)
    return make_app(GATEWAY_PDHC_SERVICE_KEY="right").test_client()


@pytest.fixture
def captured(monkeypatch):
    """Records the headers the fan-out would put on the wire."""
    seen: dict = {}

    def _fake_fanout(registry, **kw):
        seen.update(kw.get("extra_headers") or {})
        return type("R", (), {"results": []})()

    import app.analyse.canonical as c
    import app.analyse.openehr as o
    for mod in (c, o):
        monkeypatch.setattr(mod, "fanout", _fake_fanout, raising=True)
        monkeypatch.setattr(mod, "CdrRegistry", _FakeRegistry, raising=True)
    return seen, make_app(GATEWAY_PDHC_SERVICE_KEY="right").test_client()


class TestItFailsClosed:

    @pytest.mark.parametrize("path", PATIENT_ENDPOINTS)
    def test_no_purpose_is_refused(self, svc, path):
        r = svc.get(path, headers=AUTH)
        assert r.status_code == 400, \
            f"{path} served patient rows with no declared purpose"
        assert r.get_json()["error"] == "purpose_required"

    @pytest.mark.parametrize("path", PATIENT_ENDPOINTS)
    def test_the_refusal_names_the_valid_values(self, svc, path):
        """A caller that has not been updated must be able to fix itself from
        the response alone — that is the whole argument for failing closed
        rather than guessing a purpose."""
        msg = svc.get(path, headers=AUTH).get_json()["message"]
        for value in DECLARABLE_SERVICE_PURPOSES:
            assert value in msg

    @pytest.mark.parametrize("path", PATIENT_ENDPOINTS)
    def test_a_primary_use_purpose_is_not_declarable(self, svc, path):
        """cdr's reasoning, mirrored: letting a service declare 'care' would
        turn the gate into a bypass. A care-delivery read is cdr's clinical
        read, not this endpoint."""
        r = svc.get(path, headers={**AUTH, PURPOSE_HEADER: "care"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "purpose_not_declarable"

    @pytest.mark.parametrize("path", PATIENT_ENDPOINTS)
    def test_research_without_projects_is_refused(self, svc, path):
        """Consent is granted per project, not to research in general."""
        r = svc.get(path, headers={**AUTH, PURPOSE_HEADER: "research"})
        assert r.status_code == 400
        assert r.get_json()["error"] == "research_projects_required"


class TestTheDeclarationActuallyTravels:
    """Validating the header and then not forwarding it would be worse than
    not validating: it would look enforced and filter nothing."""

    @pytest.mark.parametrize("path", PATIENT_ENDPOINTS)
    def test_the_purpose_reaches_every_cdr(self, captured, path):
        seen, cl = captured
        cl.get(path, headers={**AUTH, PURPOSE_HEADER: "statistics"})
        assert seen.get(PURPOSE_HEADER) == "statistics", \
            f"{path} validated the purpose and did not forward it"

    def test_research_project_guids_travel_too(self, captured):
        seen, cl = captured
        cl.get(PATIENT_ENDPOINTS[0],
               headers={**AUTH, PURPOSE_HEADER: "research",
                        RESEARCH_PROJECTS_HEADER: "proj-a,proj-b"})
        assert seen.get(RESEARCH_PROJECTS_HEADER) == "proj-a,proj-b"


class TestStatsIsDeliberatelyExempt:

    def test_stats_needs_no_purpose(self, svc, monkeypatch):
        """It returns row counts per table and no patient rows, so there is
        nothing for a consent join to filter. Asserted so that a later
        tightening is a deliberate decision rather than a reflex."""
        import app.analyse.stats as st
        monkeypatch.setattr(
            st, "fanout",
            lambda *a, **k: type("R", (), {"results": []})(), raising=True)
        r = svc.get("/api/v1/stats", headers=AUTH)
        assert r.status_code == 200


def test_the_declarable_set_matches_cdrs():
    """Mirrored rather than imported, because analyse must not depend on
    cdr's source tree. If cdr's set changes, this is where it shows up."""
    assert DECLARABLE_SERVICE_PURPOSES == {
        "research", "statistics", "quality_registry"}
