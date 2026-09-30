"""#717 — the spärr gate called an ips endpoint that has never existed.

`excluded_by_spärr` POSTed to `/api/v1/blocks/check-bulk`. ips has no bulk
endpoint at all; the only predicate is

    GET /api/v1/patients/<guid>/blocks/check?source_clinic_id=<org>
    Authorization: ApiKey <key>

Two mistakes, both of which request.pdhc had made before (its ips_client
docstring records them): the wrong endpoint, and a header ips ignores — it
reads ONLY `Authorization`. request.pdhc's version failed OPEN, so no
ServiceRequest was ever hidden. This one failed CLOSED, so a node excluded
every patient on every run and could never return a row.

These assert the contract rather than the plumbing, because the plumbing was
never the problem.
"""
from __future__ import annotations

import pytest

from app.node.reader import ConsentUnavailable, NodeReader


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {}

    def json(self):
        return self._p


def _reader():
    return NodeReader(base_url="http://cdr.invalid", service_key="k")


class TestItCallsTheEndpointThatExists:

    def test_the_url_method_and_auth_header(self, monkeypatch):
        seen = {}

        def fake_get(url, params=None, headers=None, timeout=None, **kw):
            seen["url"], seen["params"], seen["headers"] = url, params, headers
            return _Resp(200, {"is_blocked": False})

        import requests
        monkeypatch.setattr(requests, "get", fake_get)
        _reader().excluded_by_spärr(["p1"], "https://ips.test",
                                    source_clinic_id="org-1", ips_api_key="K")

        assert seen["url"] == "https://ips.test/api/v1/patients/p1/blocks/check"
        assert seen["params"] == {"source_clinic_id": "org-1"}
        # ips reads ONLY Authorization. X-API-Key is silently ignored, which
        # is how request.pdhc's filter failed open for months.
        assert seen["headers"]["Authorization"] == "ApiKey K"
        assert "X-API-Key" not in seen["headers"]

    def test_a_blocked_patient_is_returned(self, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _Resp(200, {"is_blocked": True}))
        got = _reader().excluded_by_spärr(["p1", "p2"], "https://ips.test",
                                          source_clinic_id="o", ips_api_key="K")
        assert got == {"p1", "p2"}

    def test_404_means_no_blocks_not_an_outage(self, monkeypatch):
        """A patient ips has never heard of genuinely has no blocks. Treating
        it as an outage would exclude every patient of a new source."""
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(404))
        assert _reader().excluded_by_spärr(
            ["p1"], "https://ips.test",
            source_clinic_id="o", ips_api_key="K") == set()


class TestItFailsClosed:
    """The direction matters and it is the opposite of request.pdhc's. For a
    service request, failing open shows a row that should have been hidden;
    for analysis, an aggregate computed over a blocked patient has already
    used their data even if the number is thrown away."""

    def test_an_unexpected_status_blocks_everyone(self, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(500))
        with pytest.raises(ConsentUnavailable, match="treated as blocked"):
            _reader().excluded_by_spärr(["p1"], "https://ips.test",
                                        source_clinic_id="o", ips_api_key="K")

    def test_a_network_failure_blocks_everyone(self, monkeypatch):
        import requests

        def boom(*a, **k):
            raise requests.RequestException("ips down")
        monkeypatch.setattr(requests, "get", boom)
        with pytest.raises(ConsentUnavailable):
            _reader().excluded_by_spärr(["p1"], "https://ips.test",
                                        source_clinic_id="o", ips_api_key="K")

    @pytest.mark.parametrize("missing,match", [
        ("ips_base_url", "IPS_BASE_URL"),
        ("source_clinic_id", "source_clinic_id"),
        ("ips_api_key", "IPS_API_KEY"),
    ])
    def test_missing_configuration_refuses_rather_than_skipping(
            self, missing, match):
        """Each of the three is required to get a verdict at all. Missing one
        must not mean "no blocks found" — that is the failure mode where a
        gate silently stops gating."""
        kw = {"ips_base_url": "https://ips.test",
              "source_clinic_id": "o", "ips_api_key": "K"}
        kw[missing] = ""
        base = kw.pop("ips_base_url")
        with pytest.raises(ConsentUnavailable, match=match):
            _reader().excluded_by_spärr(["p1"], base, **kw)

    def test_no_patients_is_not_an_error(self):
        """An empty cohort needs no verdict, and demanding configuration for
        a question nobody asked would break every empty run."""
        assert _reader().excluded_by_spärr([], "", source_clinic_id=None,
                                           ips_api_key=None) == set()


class TestThePolicyCarriesTheSource:

    def test_source_clinic_id_is_read_from_the_policy_file(self):
        from app.node.policy import NodePolicy
        p = NodePolicy.load(
            "node_id: n1\ncdr_base_url: u\npermitted_purposes: [statistics]\n"
            "source_clinic_id: org-uppsala\n", is_text=True)
        assert p.source_clinic_id == "org-uppsala"

    def test_it_is_absent_by_default_so_an_unconfigured_node_refuses(self):
        from app.node.policy import NodePolicy
        p = NodePolicy.load(
            "node_id: n1\ncdr_base_url: u\npermitted_purposes: [statistics]\n",
            is_text=True)
        assert p.source_clinic_id is None
