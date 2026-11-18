"""Histogram, scatter and export count only the cohort's members (2026-11-18).

Before, the histogram took every Observation the caller could read, and
scatter and export were deliberately lenient. Each test puts a non-member
("z") in the CDR answer and asserts it is left out."""
from __future__ import annotations

from app.analyse.federation import FanoutResponse, FanoutResult

CODE = "https://termbank.pdhc.se/CodeSystem/loinc/4548-4"


def _bundle(rows):
    return {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Observation",
                      "subject": {"reference": f"Patient/{p}"},
                      "code": {"coding": [{"system": "https://termbank.pdhc.se/CodeSystem/loinc", "code": "4548-4"}]},
                      "effectiveDateTime": "2026-11-01T08:00:00Z",
                      "valueQuantity": {"value": v, "unit": "%"}}}
        for p, v in rows]}


def _fanout(monkeypatch, rows):
    from app.routes import researcher as r

    def fake(registry, **k):
        res = [FanoutResult(cdr_id="cdr2", base_url="http://x", region_label="cdr2",
                            ok=True, status_code=200, body=_bundle(rows),
                            elapsed_ms=1)]
        return FanoutResponse(mode="complete", results=res,
                              succeeded=["cdr2"], failed=[])
    monkeypatch.setattr(r, "fanout", fake)


def _cohort(client, ips_patients):
    ips_patients({"a", "b"})
    resp = client.post("/api/cohort", json={"cdr_ids": [], "demographics": {}})
    assert resp.status_code == 201, resp.get_data(as_text=True)[:300]
    return resp.get_json()["cohort_id"]


ROWS = [("a", 6.0), ("b", 7.0), ("z", 99.0), ("z", 98.0)]


def test_histogram_counts_members_only(client, ips_patients, monkeypatch):
    cid = _cohort(client, ips_patients)
    _fanout(monkeypatch, ROWS)
    b = client.get(f"/api/cohort/{cid}/variable/{CODE}/histogram").get_json()
    assert b["n"] == 2
    assert b["max"] < 90


def test_scatter_pairs_members_only(client, ips_patients, monkeypatch):
    cid = _cohort(client, ips_patients)
    _fanout(monkeypatch, ROWS)
    b = client.get(f"/api/cohort/{cid}/scatter?x={CODE}&y={CODE}").get_json()
    assert sorted(p["patient_guid"] for p in b["points"]) == ["a", "b"]


def test_export_writes_members_only(client, ips_patients, monkeypatch):
    cid = _cohort(client, ips_patients)
    _fanout(monkeypatch, ROWS)
    resp = client.get(f"/api/cohort/{cid}/export?variables={CODE}")
    assert resp.status_code == 200
    lines = resp.get_data(as_text=True).strip().splitlines()[1:]
    assert sorted(l.split(",")[0] for l in lines) == ["a", "b"]
