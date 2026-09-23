"""#684 (AN-12) — the node's HTTP surface, at the route level."""
from __future__ import annotations

import pytest

from app import create_app
from app.node.cohort_source import StaticCohortSource
from app.node.service import NodeContext
from app.routes.node_api import EXTENSION_KEY
from app.testing import synth
from app.transport.envelope import REQUEST_KIND, RESPONSE_KIND, seal, unseal

SECRET = "n" * 40
PROJECT = "synth"


def _app(role="node", secret=SECRET, with_context=True):
    app = create_app({
        "TESTING": True, "AUTH_MODE": "off",
        "DATABASE_URL": "sqlite:///:memory:",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "ANALYSE_ROLE": role,
        "ANALYSE_TRANSPORT_SECRET": secret,
    })
    if with_context:
        src = synth.build(nodes=1, patients=60, seed=1)[0]
        app.extensions[EXTENSION_KEY] = NodeContext(
            policy=src.policy, reader=src.reader(),
            ips_base_url="http://ips.invalid",
            cohort_source=StaticCohortSource(
                r["patient_guid"] for r in src.rows))
    return app


def _spec_payload():
    return {
        "spec": {
            "title": "t", "purpose": "statistics", "sources": ["cdr1"],
            "cohort": {"include": [{"observation": "x", "op": ">=",
                                    "value": 0}]},
            "variables": [{"name": "value", "from": "value", "agg": "mean"}],
            "analyses": [{"type": "describe", "vars": ["value"]}],
        },
        "project_id": PROJECT,
    }


def _signed_payload():
    from app.coordinator.signing import sign
    from app.spec import AnalysisSpec
    p = _spec_payload()
    p["spec_signature"] = sign(AnalysisSpec.model_validate(p["spec"]),
                               SECRET.encode())
    return p


@pytest.fixture(autouse=True)
def project_key(monkeypatch):
    monkeypatch.setenv(f"ANALYSE_PROJECT_KEY_{PROJECT.upper()}", "s" * 32)


class TestTheEnvelopeIsTheAuthentication:

    def test_an_unsigned_post_is_refused(self):
        c = _app().test_client()
        r = c.post("/api/v1/node/run", json=_spec_payload())
        assert r.status_code == 401
        assert r.get_json()["error"] == "envelope_rejected"

    def test_a_wrong_secret_is_refused(self):
        body, headers = seal(_signed_payload(), ("z" * 40).encode(),
                             kind=REQUEST_KIND)
        r = _app().test_client().post("/api/v1/node/run", data=body,
                                      headers=headers)
        assert r.status_code == 401

    def test_a_sealed_request_is_answered_and_sealed_back(self):
        body, headers = seal(_signed_payload(), SECRET.encode(),
                             kind=REQUEST_KIND)
        r = _app().test_client().post("/api/v1/node/run", data=body,
                                      headers=headers)
        assert r.status_code == 200
        out = unseal(r.data, r.headers, SECRET.encode(), kind=RESPONSE_KIND)
        assert out["node_id"] == "cdr1"
        assert out["partials"]
        assert out["policy"]["k_min"] == 5

    def test_an_error_response_is_not_sealed(self):
        """A verified envelope must not be usable as a stand-in for a
        verified answer."""
        c = _app().test_client()
        r = c.post("/api/v1/node/run", json=_spec_payload())
        assert "X-Analyse-Signature" not in r.headers


class TestRoleGating:

    def test_a_coordinator_does_not_serve_the_node_route(self):
        """A second, quieter way into the data, reachable by anyone holding
        the transport secret, is not something a coordinator should offer."""
        r = _app(role="coordinator", with_context=False).test_client().post(
            "/api/v1/node/run", data=b"{}")
        assert r.status_code == 404

    def test_a_node_serves_it(self):
        assert any(str(r) == "/api/v1/node/run"
                   for r in _app().url_map.iter_rules())


class TestRefusals:

    def test_an_unvalidatable_spec_is_refused_with_a_reason(self):
        payload = {"spec": {"title": "t"}, "project_id": PROJECT}
        body, headers = seal(payload, SECRET.encode(), kind=REQUEST_KIND)
        r = _app().test_client().post("/api/v1/node/run", data=body,
                                      headers=headers)
        assert r.status_code == 403
        assert "does not validate" in r.get_json()["message"]

    def test_a_missing_project_id_is_refused(self):
        payload = _signed_payload()
        payload.pop("project_id")
        body, headers = seal(payload, SECRET.encode(), kind=REQUEST_KIND)
        r = _app().test_client().post("/api/v1/node/run", data=body,
                                      headers=headers)
        assert r.status_code == 403
        assert "project_id" in r.get_json()["message"]

    def test_a_node_without_a_transport_secret_refuses_to_talk(self):
        app = _app(secret="")
        body, headers = seal(_signed_payload(), SECRET.encode(),
                             kind=REQUEST_KIND)
        r = app.test_client().post("/api/v1/node/run", data=body,
                                   headers=headers)
        assert r.status_code == 503
        assert "ANALYSE_TRANSPORT_SECRET" in r.get_json()["message"]

    def test_an_unconfigured_node_says_so_rather_than_answering(self):
        app = _app(with_context=False)
        body, headers = seal(_signed_payload(), SECRET.encode(),
                             kind=REQUEST_KIND)
        r = app.test_client().post("/api/v1/node/run", data=body,
                                   headers=headers)
        assert r.status_code == 503
        assert r.get_json()["error"] == "not_configured"
