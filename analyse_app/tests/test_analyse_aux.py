"""Federated auxiliary endpoints (#291/#292) — service-key gated.

/api/v1/observations, /api/v1/stats, /api/v1/canonical/<table>, and the two
openEHR composition search endpoints. Ported from dashboard.pdhc — a single
request returns a merged view across CDR2–6. Service-key auth via
X-Source-Service + X-Service-Key (KNOWN_SERVICES).
"""
from __future__ import annotations

from unittest.mock import patch

from app.analyse.federation import FanoutResult, FanoutResponse

from tests.conftest import make_app


def _app():
    return make_app(
        GATEWAY_PDHC_SERVICE_KEY="test-gw-key",
        CDR_ENDPOINTS=[
            {"cdr_id": "cdr1", "base_url": "http://cdr1.example"},
            {"cdr_id": "cdr2", "base_url": "http://cdr2.example"},
        ],
    )


def _hdr(service="gateway.pdhc", key="test-gw-key"):
    return {"X-Source-Service": service, "X-Service-Key": key}


def _fan(ok_bodies):
    results = [
        FanoutResult(cdr_id=f"cdr{i+1}", base_url=f"http://cdr{i+1}",
                     region_label="", ok=True, status_code=200,
                     body=b, elapsed_ms=10)
        for i, b in enumerate(ok_bodies)
    ]
    return FanoutResponse(mode="complete", results=results,
                          succeeded=[r.cdr_id for r in results], failed=[])


# --- auth ------------------------------------------------------------------

def test_stats_requires_service_key():
    r = _app().test_client().get("/api/v1/stats")
    assert r.status_code in (401, 403)


def test_stats_rejects_unknown_source():
    r = _app().test_client().get(
        "/api/v1/stats",
        headers={"X-Source-Service": "evil.pdhc", "X-Service-Key": "x"})
    assert r.status_code in (401, 403)


def test_stats_rejects_wrong_key():
    r = _app().test_client().get(
        "/api/v1/stats",
        headers={"X-Source-Service": "gateway.pdhc", "X-Service-Key": "wrong"})
    assert r.status_code == 403


# --- stats -----------------------------------------------------------------

def test_stats_sums_across_cdrs():
    fan = _fan([
        {"ingest_raw": 100, "fhir_resources": 200, "openehr_compositions": 5,
         "health_observations": 10, "dedupe_registry": 50, "patients": 3},
        {"ingest_raw": 50, "fhir_resources": 150, "openehr_compositions": 0,
         "health_observations": 5, "dedupe_registry": 20, "patients": 2},
    ])
    with patch("app.analyse.stats.fanout", return_value=fan):
        r = _app().test_client().get("/api/v1/stats", headers=_hdr())
    assert r.status_code == 200
    body = r.get_json()
    assert body["ingest_raw"] == 150
    assert body["fhir_resources"] == 350
    assert body["patients"] == 5
    assert body["cdrs_total"] == 2
    assert body["cdrs_responded"] == 2
    assert body["mode"] == "complete"


# --- canonical -------------------------------------------------------------

def test_canonical_unknown_table_404():
    r = _app().test_client().get(
        "/api/v1/canonical/nope?patient_guid=p1", headers=_hdr())
    assert r.status_code == 404


def test_canonical_missing_patient_400():
    r = _app().test_client().get(
        "/api/v1/canonical/health_observations", headers=_hdr())
    assert r.status_code == 400


def test_canonical_merges_rows_with_cdr_tag():
    fan = _fan([
        {"table": "health_observations", "patient_guid": "p1",
         "rows": [{"guid": "g1", "value": 1.0}]},
        {"table": "health_observations", "patient_guid": "p1",
         "rows": [{"guid": "g2", "value": 2.0}, {"guid": "g3", "value": 3.0}]},
    ])
    with patch("app.analyse.canonical.fanout", return_value=fan):
        r = _app().test_client().get(
            "/api/v1/canonical/health_observations?patient_guid=p1",
            headers=_hdr())
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 3
    assert {row["_cdr_id"] for row in body["rows"]} == {"cdr1", "cdr2"}


# --- openehr ---------------------------------------------------------------

def test_openehr_composition_search_requires_patient():
    r = _app().test_client().get("/api/v1/openehr/composition", headers=_hdr())
    assert r.status_code == 400


def test_openehr_composition_search_merges():
    fan = _fan([
        {"compositions": [{"guid": "c-a"}, {"guid": "c-b"}]},
        {"compositions": [{"guid": "c-c"}]},
    ])
    with patch("app.analyse.openehr.fanout", return_value=fan):
        r = _app().test_client().get(
            "/api/v1/openehr/composition?patient=p-1", headers=_hdr())
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 3
    assert {x["_cdr_id"] for x in body["compositions"]} == {"cdr1", "cdr2"}


# --- observations search (#291) --------------------------------------------

def test_observations_requires_service_request():
    r = _app().test_client().get("/api/v1/observations", headers=_hdr())
    assert r.status_code == 400


def test_observations_filters_by_service_request():
    obs_match = {"resourceType": "Observation",
                 "basedOn": [{"identifier": {"value": "sr-1"}}]}
    obs_other = {"resourceType": "Observation",
                 "basedOn": [{"identifier": {"value": "sr-999"}}]}
    fan = _fan([
        {"entry": [{"resource": obs_match}, {"resource": obs_other}]},
        {"entry": [{"resource": obs_match}]},
    ])
    with patch("app.analyse.observations_search.fanout", return_value=fan):
        r = _app().test_client().get(
            "/api/v1/observations?service_request=sr-1", headers=_hdr())
    assert r.status_code == 200
    body = r.get_json()
    assert body["resourceType"] == "Bundle"
    # sr-1 matched twice (once per CDR); sr-999 filtered out.
    assert body["total"] == 2
