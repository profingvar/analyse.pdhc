"""Cohort preview, CDR choice and minimum-measures filter (2026-11-18)."""
from __future__ import annotations

from app.analyse import measure_counts as mc
from app.analyse.federation import FanoutResponse, FanoutResult

PLAN = "urn:pdhc:concept/22d0f6c6-2438-4637-ad4e-9d85431b8edb"
LOINC = "https://termbank.pdhc.se/CodeSystem/loinc/4548-4"


def _fr(cdr_id, body, ok=True):
    return FanoutResult(cdr_id=cdr_id, base_url="http://x", region_label=cdr_id,
                        ok=ok, status_code=200 if ok else 500, body=body,
                        elapsed_ms=1, error=None if ok else "500")


def _counts_body(rows):
    return {"resourceType": "PdhcCounts", "counts": [
        {"patient": p, "code": c, "n": n} for p, c, n in rows]}


def _bundle(rows):
    return {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Observation",
                      "subject": {"reference": f"Patient/{p}"},
                      "code": {"coding": [{"system": s, "code": c}]}}}
        for p, s, c in rows]}


# --- pure ----------------------------------------------------------------

def test_counts_from_a_counting_cdr_and_from_rows_are_merged():
    counts, src = mc.collect([
        _fr("cdr2", _counts_body([("a", PLAN, 5), ("b", LOINC, 2)])),
        # cdr_6 spells the system with a trailing slash; the same concept.
        _fr("cdr6", _bundle([("a", "urn:pdhc:concept/", PLAN.rsplit("/", 1)[1])] * 3)),
    ])
    assert counts == {"a": {PLAN: 8}, "b": {LOINC: 2}}
    modes = {s["cdr_id"]: s["mode"] for s in src}
    assert modes == {"cdr2": "counted_by_cdr", "cdr6": "counted_from_rows"}


def test_a_full_row_page_is_marked_truncated(monkeypatch):
    monkeypatch.setattr(mc, "ROW_PAGE", 2)
    _, src = mc.collect([_fr("cdr6", _bundle([("a", "s", "1"), ("a", "s", "1")]))])
    assert src[0]["truncated"] is True


def test_a_failed_cdr_is_reported_not_counted():
    counts, src = mc.collect([_fr("cdr3", None, ok=False)])
    assert counts == {} and src[0]["ok"] is False


def test_min_zero_keeps_everyone_including_patients_without_measures():
    assert mc.keep({"a", "none"}, {"a": {PLAN: 1}}, 0) == {"a", "none"}


def test_min_measures_counts_the_total_over_concepts():
    counts = {"a": {PLAN: 2, LOINC: 1}, "b": {PLAN: 2}}
    assert mc.keep({"a", "b", "c"}, counts, 3) == {"a"}


def test_summary_lists_every_candidate_and_describes_the_kept_set():
    out = mc.summarise({"a", "b"}, {"a": {PLAN: 4}, "b": {PLAN: 1}}, 2)
    assert out["n_candidates"] == 2 and out["n"] == 1
    assert [(p["patient_guid"], p["kept"]) for p in out["patients"]] == [("a", True), ("b", False)]
    assert out["concepts"][0]["measures"] == 4 and out["concepts"][0]["patients"] == 1
    assert out["concepts"][0]["concept_guid"] == "22d0f6c6-2438-4637-ad4e-9d85431b8edb"
    assert out["max_measures"] == 4


# --- routes --------------------------------------------------------------

def _fake_fanout(calls):
    def fake(registry, **k):
        calls.append(k)
        res = [_fr("cdr2", _counts_body([("a", PLAN, 5), ("b", PLAN, 1)]))]
        return FanoutResponse(mode="complete", results=res,
                              succeeded=["cdr2"], failed=[])
    return fake


def test_preview_returns_patients_and_concepts_for_the_chosen_cdrs(client, ips_patients, monkeypatch):
    from app.routes import researcher as r
    ips_patients({"a", "b", "c"})
    calls = []
    monkeypatch.setattr(r, "fanout", _fake_fanout(calls))
    monkeypatch.setattr(r, "_fill_concept_displays", lambda c, u: 0)
    resp = client.post("/api/cohort/preview", json={"cdr_ids": ["cdr2"], "demographics": {}})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    b = resp.get_json()
    assert b["n_candidates"] == 3 and b["n"] == 3          # default: include all
    assert {p["patient_guid"]: p["total"] for p in b["patients"]} == {"a": 5, "b": 1, "c": 0}
    assert calls[0]["cdr_ids"] == ["cdr2"]
    assert calls[0]["params"]["_pdhc_counts"] == "patient,code"
    # preview saves nothing
    assert client.get("/api/cohort").get_json()["n"] == 0


def test_define_applies_min_measures(client, ips_patients, monkeypatch):
    from app.routes import researcher as r
    ips_patients({"a", "b", "c"})
    monkeypatch.setattr(r, "fanout", _fake_fanout([]))
    resp = client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}, "min_measures": 2})
    assert resp.status_code == 201
    assert resp.get_json()["n"] == 1
    assert resp.get_json()["summary"]["min_measures"]["before"] == 3


def test_define_without_min_measures_asks_no_cdr(client, ips_patients, monkeypatch):
    from app.routes import researcher as r
    ips_patients({"a", "b"})
    calls = []
    monkeypatch.setattr(r, "fanout", _fake_fanout(calls))
    resp = client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}})
    assert resp.get_json()["n"] == 2 and calls == []


def test_a_bad_min_measures_is_refused(client, ips_patients):
    ips_patients({"a"})
    r = client.post("/api/cohort", json={"demographics": {}, "min_measures": "many"})
    assert r.status_code == 400
