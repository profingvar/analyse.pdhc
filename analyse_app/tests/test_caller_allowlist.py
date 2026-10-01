"""#727 — monitor.pdhc is gone, and the allowlist lives in one place.

``monitor.pdhc`` was never a service. It was a synthetic service-key identity
created 2026-04-28 so the Playwright, perf and chaos suites could bypass SSO,
and it was allowlisted on all four federated endpoints — three of which fan a
*named patient's* rows out of every CDR. It is now removed from
``KNOWN_SERVICES`` as well, so it cannot authenticate to analyse at all.

Note what these tests are not: before #727 the allowlist had no test of any
kind. Four hand-copied literals guarding patient data, and nothing asserted
that either name was on the list or that anything else was off it.
"""
from __future__ import annotations

from tests.conftest import make_app

PATIENT_ENDPOINTS = [
    "/api/v1/canonical/health_observations?patient_guid=p1",
    "/api/v1/openehr/composition?patient=p1",
    "/api/v1/openehr/ehr/p1/compositions",
    "/api/v1/observations?service_request=sr1",
]
AGGREGATE_ENDPOINT = "/api/v1/stats"
ALL_ENDPOINTS = PATIENT_ENDPOINTS + [AGGREGATE_ENDPOINT]


def _client():
    # The monitor key is deliberately still configured here: the point is that
    # setting it changes nothing, because nothing reads it any more.
    return make_app(
        GATEWAY_PDHC_SERVICE_KEY="gw-key",
        MONITOR_PDHC_SERVICE_KEY="mon-key",
    ).test_client()


def _as(service, key):
    return {"X-Source-Service": service, "X-Service-Key": key}


MONITOR = _as("monitor.pdhc", "mon-key")
GATEWAY = _as("gateway.pdhc", "gw-key")


# --- the removal ------------------------------------------------------------

def test_monitor_cannot_reach_anything_including_stats():
    c = _client()
    for url in ALL_ENDPOINTS:
        r = c.get(url, headers=MONITOR)
        assert r.status_code == 403, f"{url} let monitor.pdhc through"


def test_monitor_is_refused_at_the_auth_layer_not_the_allowlist():
    """It is no longer a known service, so it never reaches a route.

    Worth pinning: a 403 from the route's allowlist would mean the identity
    still authenticates and only the endpoint list is holding it back.
    """
    r = _client().get(AGGREGATE_ENDPOINT, headers=MONITOR)
    assert r.status_code == 403
    assert r.get_json() == {"error": "Invalid service credentials"}


def test_monitor_key_config_is_inert():
    from app.auth import KNOWN_SERVICES
    assert "monitor.pdhc" not in KNOWN_SERVICES
    assert KNOWN_SERVICES == {"gateway.pdhc": "GATEWAY_PDHC_SERVICE_KEY"}
    # The env var may still be set on a host; nothing should consult it.
    assert "MONITOR_PDHC_SERVICE_KEY" not in make_app().config


def test_gateway_is_unaffected_on_every_endpoint():
    # #727 removes one caller; it must not touch the one real service.
    c = _client()
    for url in ALL_ENDPOINTS:
        r = c.get(url, headers=GATEWAY)
        assert r.status_code != 403, f"{url} wrongly refused gateway.pdhc"


# --- the refusal says nothing it shouldn't ----------------------------------

def test_refusal_body_names_no_caller():
    body = _client().get(PATIENT_ENDPOINTS[0], headers=MONITOR).get_data(as_text=True)
    assert "monitor" not in body
    assert "gateway" not in body


def test_no_service_key_at_all_is_401_not_403():
    # Dev mode hands out an SU blob with no service_source; the endpoints
    # self-gate on that and must not be mistaken for an allowlist miss.
    r = make_app().test_client().get(PATIENT_ENDPOINTS[0])
    assert r.status_code == 401


# --- the policy lives in one place ------------------------------------------

def test_the_allowlist_is_not_spelled_out_in_the_routes():
    """#726's lesson: four copies of one rule drift on the day a fifth caller
    is added to three of them. The literal belongs in callers.py alone."""
    import pathlib
    here = pathlib.Path(__file__).resolve().parents[1] / "app" / "analyse"
    for name in ("canonical.py", "openehr.py", "observations_search.py",
                 "stats.py"):
        src = (here / name).read_text()
        assert '"gateway.pdhc"' not in src, (
            f"{name} spells the allowlist out again — import it from callers.py"
        )


def test_aggregate_is_a_superset_of_patient_data():
    """An endpoint handing out a named patient's rows must never admit a caller
    the counts-only endpoint refuses."""
    from app.analyse.callers import AGGREGATE_CALLERS, PATIENT_DATA_CALLERS
    assert PATIENT_DATA_CALLERS <= AGGREGATE_CALLERS
    assert PATIENT_DATA_CALLERS == {"gateway.pdhc"}
