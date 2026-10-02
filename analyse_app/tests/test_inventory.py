"""/api/inventory — per-CDR availability for the researcher landing (#688).

The federated /api/v1/stats pools every CDR into one total, which is right for
a monitor and wrong for a researcher: a cohort resolves ACROSS sources, so
"which source holds what" is the thing you need before writing a predicate.

The case that matters here is the unreachable one. An inventory that silently
dropped a source, or counted it as zero, would make a cohort look smaller than
the data warrants for a reason nothing on the page could show.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_app


class _Ep:
    def __init__(self, cdr_id):
        self.cdr_id = cdr_id
        self.base_url = f"http://{cdr_id}"


class _Reg:
    def __init__(self, ids):
        self.all = [_Ep(i) for i in ids]


class _Res:
    def __init__(self, cdr_id, ok, body=None):
        self.cdr_id, self.ok, self.body = cdr_id, ok, body


class _Fan:
    def __init__(self, results):
        self.results = results


def _app_with(monkeypatch, ids, results):
    app = make_app()
    import app.routes.researcher as R
    monkeypatch.setattr(R, "_registry", lambda: _Reg(ids))
    monkeypatch.setattr(R, "fanout", lambda *a, **k: _Fan(results))
    return app


def _body(patients, obs):
    return {"patients": patients, "health_observations": obs,
            "fhir_resources": obs * 2, "openehr_compositions": 0}


def test_counts_are_reported_per_cdr_not_pooled(monkeypatch):
    app = _app_with(monkeypatch, ["cdr2", "cdr3"], [
        _Res("cdr2", True, _body(100, 2000)),
        _Res("cdr3", True, _body(50, 500)),
    ])
    r = app.test_client().get("/api/inventory")
    assert r.status_code == 200
    b = r.get_json()
    rows = {c["cdr_id"]: c for c in b["cdrs"]}
    assert rows["cdr2"]["patients"] == 100
    assert rows["cdr3"]["patients"] == 50
    assert b["totals"]["patients"] == 150
    assert b["mode"] == "complete"


def test_an_unreachable_cdr_is_named_not_dropped_or_zeroed(monkeypatch):
    app = _app_with(monkeypatch, ["cdr2", "cdr3"], [
        _Res("cdr2", True, _body(100, 2000)),
        _Res("cdr3", False, None),
    ])
    b = app.test_client().get("/api/inventory").get_json()
    rows = {c["cdr_id"]: c for c in b["cdrs"]}
    # present...
    assert set(rows) == {"cdr2", "cdr3"}
    # ...flagged...
    assert rows["cdr3"]["reachable"] is False
    # ...and None rather than 0, so the page cannot render a confident zero.
    assert rows["cdr3"]["patients"] is None
    # ...and excluded from the total rather than adding nothing silently.
    assert b["totals"]["patients"] == 100
    assert b["mode"] == "degraded"
    assert b["cdrs_responded"] == 1 and b["cdrs_total"] == 2


def test_all_sources_down_is_error_not_empty(monkeypatch):
    app = _app_with(monkeypatch, ["cdr2"], [_Res("cdr2", False, None)])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["mode"] == "error"
    assert b["cdrs"][0]["reachable"] is False


def test_no_sources_configured_is_empty(monkeypatch):
    app = _app_with(monkeypatch, [], [])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["mode"] == "empty" and b["cdrs"] == []


def test_a_non_numeric_count_does_not_crash_or_fake_a_number(monkeypatch):
    app = _app_with(monkeypatch, ["cdr2"], [
        _Res("cdr2", True, {"patients": "lots", "health_observations": 7}),
    ])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["cdrs"][0]["patients"] is None
    assert b["cdrs"][0]["health_observations"] == 7
