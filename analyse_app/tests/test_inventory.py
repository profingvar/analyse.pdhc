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
    # #756 renamed the reported key to `observations`; the fallback means a CDR
    # still sending only the old name is read, not counted as zero.
    assert b["cdrs"][0]["observations"] == 7


def test_a_non_federated_cdr_is_named_with_its_reason(monkeypatch):
    """cdr1 is absent from CDR_ENDPOINTS on purpose (#712), and the first
    version of this page simply did not mention it.

    A reader could not distinguish "that CDR does not exist" from "that CDR is
    deliberately out of scope" — and the omitted one holds the real
    multi-organisation data. An absence must not be silent.
    """
    app = _app_with(monkeypatch, ["cdr2"], [_Res("cdr2", True, _body(10, 20))])
    b = app.test_client().get("/api/inventory").get_json()
    ex = {e["cdr_id"]: e["reason"] for e in b["excluded"]}
    assert "cdr1" in ex
    assert "care-delivery" in ex["cdr1"]
    # It is NOT counted — naming it must not quietly inflate the totals.
    assert b["totals"]["patients"] == 10
    assert all(c["cdr_id"] != "cdr1" for c in b["cdrs"])


def test_a_cdr_that_IS_federated_is_not_also_listed_as_excluded(monkeypatch):
    """If cdr1 is ever added to CDR_ENDPOINTS, it must stop being reported as
    out of scope — the two lists cannot both claim it."""
    app = _app_with(monkeypatch, ["cdr1", "cdr2"], [
        _Res("cdr1", True, _body(5, 5)), _Res("cdr2", True, _body(10, 20))])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["excluded"] == []
    assert {c["cdr_id"] for c in b["cdrs"]} == {"cdr1", "cdr2"}


def test_the_docstring_example_does_not_point_at_a_non_federated_cdr():
    """app/analyse/cohort.py's example predicate used `["cdr1", "cdr3"]`, which
    pointed a reader straight at the source this coordinator does not serve."""
    import app.analyse.cohort as C
    from app.analyse.federation import NON_FEDERATED_CDRS
    doc = C.__doc__ or ""
    example = doc[doc.find('"cdr_ids"'):doc.find('"demographics"')]
    for cid in NON_FEDERATED_CDRS:
        assert f'"{cid}"' not in example, (
            f"the example predicate still suggests {cid}")


# --- #733: WHAT a researcher can ask about, not just how much -------------

class _CRes:
    def __init__(self, cdr_id, ok, body=None):
        self.cdr_id, self.ok, self.body = cdr_id, ok, body


def _two_fanouts(monkeypatch, ids, stats_results, concept_results):
    """fanout() is called twice — once for /stats, once for the inventory."""
    app = make_app()
    import app.routes.researcher as R
    monkeypatch.setattr(R, "_registry", lambda: _Reg(ids))
    calls = {"n": 0}

    def fake(registry, **kw):
        calls["n"] += 1
        return _Fan(concept_results if "concept" in (kw.get("path") or "")
                    else stats_results)
    monkeypatch.setattr(R, "fanout", fake)
    return app


def _concept(canon, obs, units=None, display=None, patients="<5"):
    return {"code_canonical": canon, "observations": obs, "patients": patients,
            "units": units or {}, "display": display}


def test_a_concept_held_by_two_cdrs_reads_as_held_by_two(monkeypatch):
    """Union by code_canonical, never summed into one blob — a concept present
    in two sources should say so, same reasoning as the unpooled counts."""
    app = _two_fanouts(monkeypatch, ["cdr2", "cdr3"],
                       [_Res("cdr2", True, _body(5, 5)), _Res("cdr3", True, _body(5, 5))],
                       [_CRes("cdr2", True, {"concepts": [_concept("urn:x", 10)]}),
                        _CRes("cdr3", True, {"concepts": [_concept("urn:x", 7)]})])
    b = app.test_client().get("/api/inventory").get_json()
    c = [x for x in b["concepts"] if x["code_canonical"] == "urn:x"][0]
    assert c["held_by"] == ["cdr2", "cdr3"]
    assert c["observations"] == 17


def test_two_units_for_one_concept_is_flagged(monkeypatch):
    """The disagreement the platform could not previously see at all — cdr1's
    'Reliever (SABA) puffs in last 24h' carries both '/d' and 'doser'."""
    app = _two_fanouts(monkeypatch, ["cdr2"], [_Res("cdr2", True, _body(5, 5))],
                       [_CRes("cdr2", True, {"concepts": [
                           _concept("urn:y", 9, units={"/d": 5, "doser": 4})]})])
    c = app.test_client().get("/api/inventory").get_json()["concepts"][0]
    assert c.get("unit_disagreement") is True
    assert c["units"] == {"/d": 5, "doser": 4}


def test_patient_counts_are_not_summed_across_cdrs(monkeypatch):
    """They arrive already k-floored per CDR. Adding floored values gives a
    number that is neither the truth nor a floor."""
    app = _two_fanouts(monkeypatch, ["cdr2", "cdr3"],
                       [_Res("cdr2", True, _body(5, 5)), _Res("cdr3", True, _body(5, 5))],
                       [_CRes("cdr2", True, {"concepts": [_concept("urn:z", 1)]}),
                        _CRes("cdr3", True, {"concepts": [_concept("urn:z", 1)]})])
    c = app.test_client().get("/api/inventory").get_json()["concepts"][0]
    assert "patients" not in c, "a summed patient count must not be published"


def test_a_cdr_without_the_endpoint_does_not_break_the_page(monkeypatch):
    app = _two_fanouts(monkeypatch, ["cdr2"], [_Res("cdr2", True, _body(5, 5))],
                       [_CRes("cdr2", False, None)])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["concepts"] == []
    assert b["cdrs"][0]["patients"] == 5, "the counts half must still work"


def test_the_merge_keeps_how_each_code_is_bound(monkeypatch):
    """cdr1 holds Path-B concept GUIDs, cdr2/cdr3 hold termbank LOINC URIs for
    all 2.6M of their observations. A merged list that dropped `binding` would
    present the two as one kind and leave a researcher unable to tell which
    vocabulary a predicate is written against."""
    LOINC = "https://termbank.pdhc.se/CodeSystem/loinc/41653-7"
    PLAN = "urn:pdhc:concept/38743fbb-4474-4988-8eeb-292dd9b249a7"
    app = _two_fanouts(
        monkeypatch, ["cdr2", "cdr3"],
        [_Res("cdr2", True, _body(5, 5)), _Res("cdr3", True, _body(5, 5))],
        [_CRes("cdr2", True, {"concepts": [{
            "code_canonical": LOINC, "display": "loinc 41653-7",
            "binding": "external_code", "code_system_name": "loinc",
            "code": "41653-7", "observations": 2592000,
            "units": {"mmol/L": 2592000}}]}),
         _CRes("cdr3", True, {"concepts": [{
             "code_canonical": PLAN, "display": "B-glucos",
             "binding": "plan_concept",
             "concept_guid": "38743fbb-4474-4988-8eeb-292dd9b249a7",
             "observations": 7134, "units": {"mmol/L": 7134}}]})])
    b = app.test_client().get("/api/inventory").get_json()
    by_key = {c["code_canonical"]: c for c in b["concepts"]}
    assert by_key[LOINC]["binding"] == "external_code"
    assert by_key[LOINC]["code"] == "41653-7"
    assert by_key[LOINC]["code_system_name"] == "loinc"
    assert by_key[PLAN]["binding"] == "plan_concept"
    assert by_key[PLAN]["concept_guid"]


def test_inventory_reads_the_live_observation_count(monkeypatch):
    """#756: cdr1/2/3 reported `health_observations` from an empty legacy table
    while cdr_6 reported `observations`. The landing showed 0 for every source
    beside a concept list totalling millions."""
    app = _app_with(monkeypatch, ["cdr2", "cdr6"], [
        _Res("cdr2", True, {"patients": 10, "observations": 2629300,
                            "fhir_resources": 5, "openehr_compositions": 0}),
        _Res("cdr6", True, {"patients": 11336, "observations": 1285296,
                            "fhir_resources": 0, "openehr_compositions": 0}),
    ])
    b = app.test_client().get("/api/inventory").get_json()
    rows = {c["cdr_id"]: c for c in b["cdrs"]}
    assert rows["cdr2"]["observations"] == 2629300
    assert rows["cdr6"]["observations"] == 1285296
    assert b["totals"]["observations"] == 2629300 + 1285296


def test_a_cdr_not_yet_redeployed_falls_back(monkeypatch):
    """A CDR still returning only the old key must not read as zero — that is
    the failure #756 describes, and a rolling deploy would recreate it."""
    app = _app_with(monkeypatch, ["cdr2"], [
        _Res("cdr2", True, {"patients": 10, "health_observations": 99,
                            "fhir_resources": 5, "openehr_compositions": 0})])
    b = app.test_client().get("/api/inventory").get_json()
    assert b["cdrs"][0]["observations"] == 99
