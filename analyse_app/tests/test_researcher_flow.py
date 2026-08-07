"""Integration-shaped tests for the ported researcher/cohort engine (#539).

Patches ``app.analyse.federation.requests.request`` end-to-end and walks the
cohort flow: define → list → histogram merge across 2 CDRs → export CSV
header → scatter truncation. AUTH_MODE=off admits the request as dev-SU
(is_su_admin ⇒ researcher_required passes); consent is allow-all patched in
conftest.
"""
from __future__ import annotations

import csv
import io
from unittest.mock import patch

import pytest

from tests.conftest import make_app


@pytest.fixture
def app():
    return make_app(CDR_ENDPOINTS=[
        {"cdr_id": "cdr1", "base_url": "http://cdr1", "region_label": "Norrland"},
        {"cdr_id": "cdr2", "base_url": "http://cdr2", "region_label": "Skåne"},
    ])


@pytest.fixture
def client(app):
    return app.test_client()


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return ""


def _mk_patient(guid):
    return {"resourceType": "Patient", "id": guid, "active": True}


def _mk_obs(pat, code, value, eff="2026-04-01T10:00:00+00:00",
            system="https://termbank.pdhc.se/CodeSystem/loinc"):
    return {
        "resourceType": "Observation",
        "subject": {"reference": f"Patient/{pat}"},
        "code": {"coding": [{"system": system, "code": code}]},
        "effectiveDateTime": eff,
        "valueQuantity": {"value": value, "unit": "%", "code": "%"},
    }


def _dispatch(routes):
    def _req(method, url, params=None, json=None, headers=None, timeout=None):
        for predicate, body in routes:
            if predicate(method, url, params or {}, json or {}):
                return _FakeResp(200, body)
        return _FakeResp(404, {})
    return _req


def test_define_cohort_returns_id_and_count(client):
    routes = [
        (lambda m, u, p, j: "/api/v1/fhir/Patient" in u and "cdr1" in u,
         {"entry": [{"resource": _mk_patient("p1")},
                    {"resource": _mk_patient("p2")}]}),
        (lambda m, u, p, j: "/api/v1/fhir/Patient" in u and "cdr2" in u,
         {"entry": [{"resource": _mk_patient("p3")}]}),
        (lambda m, u, p, j: "/api/v1/fhir/Condition" in u and "cdr1" in u,
         {"entry": [{"resource": {"resourceType": "Condition",
                                  "subject": {"reference": "Patient/p2"}}}]}),
        (lambda m, u, p, j: "/api/v1/fhir/Condition" in u and "cdr2" in u,
         {"entry": [{"resource": {"resourceType": "Condition",
                                  "subject": {"reference": "Patient/p3"}}}]}),
    ]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes)):
        resp = client.post("/api/cohort", json={
            "cdr_ids": ["cdr1", "cdr2"],
            "conditions": ["https://termbank.pdhc.se/CodeSystem/snomed/44054006"],
        })
    assert resp.status_code == 201
    # Intersection of {p1,p2,p3} and {p2,p3} = {p2,p3}.
    assert resp.get_json()["n"] == 2


def test_list_cohorts(client):
    routes = [(lambda m, u, p, j: "/api/v1/fhir/Patient" in u,
               {"entry": [{"resource": _mk_patient("p1")}]})]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes)):
        client.post("/api/cohort", json={"cdr_ids": ["cdr1"]})
    r = client.get("/api/cohort")
    assert r.status_code == 200
    assert r.get_json()["n"] >= 1


def test_cohort_histogram_merges_two_cdrs(client):
    routes_define = [(lambda m, u, p, j: "/api/v1/fhir/Patient" in u,
                      {"entry": [{"resource": _mk_patient("p1")}]})]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes_define)):
        cid = client.post("/api/cohort",
                          json={"cdr_ids": ["cdr1", "cdr2"]}).get_json()["cohort_id"]

    a = {"resourceType": "Bundle", "entry": [
        {"resource": _mk_obs(f"p{i}", "4548-4", 5.0 + (i % 30) * 0.1)}
        for i in range(100)]}
    b = {"resourceType": "Bundle", "entry": [
        {"resource": _mk_obs(f"q{i}", "4548-4", 6.0 + (i % 30) * 0.1)}
        for i in range(80)]}
    routes_hist = [
        (lambda m, u, p, j: "/api/v1/fhir/Observation" in u and "cdr1" in u, a),
        (lambda m, u, p, j: "/api/v1/fhir/Observation" in u and "cdr2" in u, b),
    ]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes_hist)):
        h = client.get(
            f"/api/cohort/{cid}/variable/"
            "https://termbank.pdhc.se/CodeSystem/loinc/4548-4/histogram")
    assert h.status_code == 200
    body = h.get_json()
    assert body["n"] == 180
    assert sum(b["count"] for b in body["buckets"]) == 180


def test_cohort_export_csv_has_expected_header(client):
    routes_define = [(lambda m, u, p, j: "/api/v1/fhir/Patient" in u,
                      {"entry": [{"resource": _mk_patient("p1")},
                                 {"resource": _mk_patient("p2")}]})]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes_define)):
        cid = client.post("/api/cohort",
                          json={"cdr_ids": ["cdr1"]}).get_json()["cohort_id"]

    routes_export = [(lambda m, u, p, j: "/api/v1/fhir/Observation" in u,
                      {"entry": [{"resource": _mk_obs("p1", "4548-4", 6.4)},
                                 {"resource": _mk_obs("p2", "4548-4", 7.1)}]})]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes_export)):
        r = client.get(
            f"/api/cohort/{cid}/export"
            "?format=csv&variables=https://termbank.pdhc.se/CodeSystem/loinc/4548-4")
        body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/csv")
    header = list(csv.reader(io.StringIO(body)))[0]
    assert "patient_guid" in header and "canonical" in header \
        and "sim_run_id" in header


def test_scatter_truncates_above_cap(client):
    members = [f"pat-{i}" for i in range(200)]
    routes_define = [(lambda m, u, p, j: "/api/v1/fhir/Patient" in u,
                      {"entry": [{"resource": _mk_patient(g)} for g in members]})]
    with patch("app.analyse.federation.requests.request",
               side_effect=_dispatch(routes_define)):
        cid = client.post("/api/cohort",
                          json={"cdr_ids": ["cdr1"]}).get_json()["cohort_id"]

    obs_x = [_mk_obs(g, "4548-4", 6.0 + i * 0.01) for i, g in enumerate(members)]
    obs_y = [_mk_obs(g, "29463-7", 70.0 + i * 0.5) for i, g in enumerate(members)]

    def _req(method, url, params=None, json=None, headers=None, timeout=None):
        if "/api/v1/fhir/Observation" not in url:
            return _FakeResp(404, {})
        code = (params or {}).get("code") or ""
        if "4548-4" in code:
            return _FakeResp(200, {"entry": [{"resource": x} for x in obs_x]})
        if "29463-7" in code:
            return _FakeResp(200, {"entry": [{"resource": y} for y in obs_y]})
        return _FakeResp(200, {"entry": []})

    with patch("app.analyse.federation.requests.request", side_effect=_req):
        r = client.get(
            f"/api/cohort/{cid}/scatter"
            "?x=https://termbank.pdhc.se/CodeSystem/loinc/4548-4"
            "&y=https://termbank.pdhc.se/CodeSystem/loinc/29463-7&max=50")
    body = r.get_json()
    assert body["truncated"] is True
    assert body["n"] <= 50
