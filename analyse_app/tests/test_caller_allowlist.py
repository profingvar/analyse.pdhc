"""#727 — monitor.pdhc is narrowed to the aggregate endpoint.

``monitor.pdhc`` is not a service. It is a synthetic service-key identity
created 2026-04-28 so the Playwright, perf and chaos suites could bypass SSO,
and it was allowlisted on all four federated endpoints — three of which fan a
*named patient's* rows out of every CDR. It is now allowed on ``/api/v1/stats``
only.

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


def _client():
    return make_app(
        GATEWAY_PDHC_SERVICE_KEY="gw-key",
        MONITOR_PDHC_SERVICE_KEY="mon-key",
    ).test_client()


def _as(service, key):
    return {"X-Source-Service": service, "X-Service-Key": key}


MONITOR = _as("monitor.pdhc", "mon-key")
GATEWAY = _as("gateway.pdhc", "gw-key")


# --- the narrowing itself ---------------------------------------------------

def test_monitor_is_refused_on_every_patient_data_endpoint():
    c = _client()
    for url in PATIENT_ENDPOINTS:
        r = c.get(url, headers=MONITOR)
        assert r.status_code == 403, f"{url} let monitor.pdhc through"


def test_monitor_keeps_the_aggregate_endpoint():
    # A monitoring identity still needs to know the CDRs are answering.
    r = _client().get(AGGREGATE_ENDPOINT, headers=MONITOR)
    assert r.status_code == 200


def test_gateway_is_unaffected_on_every_endpoint():
    # #727 narrows one caller; it must not touch the one real service.
    c = _client()
    for url in PATIENT_ENDPOINTS + [AGGREGATE_ENDPOINT]:
        r = c.get(url, headers=GATEWAY)
        assert r.status_code != 403, f"{url} wrongly refused gateway.pdhc"


# --- the refusal says nothing it shouldn't ----------------------------------

def test_refusal_body_is_identical_for_wrong_list_and_unknown_service():
    """A rejected caller learns it is not allowed here, not who is."""
    c = _client()
    narrowed = c.get(PATIENT_ENDPOINTS[0], headers=MONITOR)
    unknown = c.get(PATIENT_ENDPOINTS[0],
                    headers=_as("gateway.pdhc", "gw-key"))
    assert narrowed.status_code == 403
    assert "monitor" not in narrowed.get_data(as_text=True)
    assert "gateway" not in narrowed.get_data(as_text=True)
    assert unknown.status_code != 403


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
        body = "\n".join(
            line for line in src.splitlines()
            if "monitor.pdhc" not in line or line.lstrip().startswith("#")
        )
        assert '"monitor.pdhc"' not in body, (
            f"{name} spells the allowlist out again — import it from callers.py"
        )


def test_patient_and_aggregate_lists_differ_only_by_monitor():
    from app.analyse.callers import AGGREGATE_CALLERS, PATIENT_DATA_CALLERS
    assert PATIENT_DATA_CALLERS == {"gateway.pdhc"}
    assert AGGREGATE_CALLERS - PATIENT_DATA_CALLERS == {"monitor.pdhc"}
    assert PATIENT_DATA_CALLERS <= AGGREGATE_CALLERS
