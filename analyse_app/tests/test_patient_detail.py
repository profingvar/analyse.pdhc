"""Unit tests for the #579 per-patient clinical detail assembler.

Observation shapes mirror the live cdr2–5 form verified in item-3: two codings
(LOINC standard + plan.pdhc Concept guid) and the owning clinic in
``meta.security[code==org_guid].display``.
"""
from types import SimpleNamespace

import app.analyse.patient_detail as pd


def _res(cdr_id, obs_list):
    return SimpleNamespace(cdr_id=cdr_id, ok=True, status_code=200,
                           body={"entry": [{"resource": o} for o in obs_list]})


def _resp(results, mode="complete"):
    return SimpleNamespace(mode=mode, results=results, succeeded=[], failed=[])


def _obs(patient, code, val, date, unit="mmol/L", label=None, org=None):
    """Live-shaped Observation: coding[0]=LOINC, coding[1]=plan.pdhc Concept."""
    o = {"resourceType": "Observation",
         "subject": {"reference": f"Patient/{patient}"},
         "code": {"coding": [
             {"code": "L-" + code, "display": label or code,
              "system": "https://termbank.pdhc.se/CodeSystem/loinc"},
             {"code": code, "display": (label or code) + " concept",
              "system": "https://plan.pdhc.se/Concept"}]},
         "valueQuantity": {"code": unit, "unit": unit, "value": val},
         "effectiveDateTime": date}
    if org is not None:
        o["meta"] = {"security": [{"code": "org_guid", "display": org,
                                   "system": "https://cdr.pdhc.se/CodeSystem/org"}]}
    return o


def _block(clinic_guid):
    return type("B", (), {"is_active": True, "source_scope_type": "clinic",
                          "source_scope_id": clinic_guid,
                          "guid": "blk-" + clinic_guid})()


class FakeReg:
    @property
    def all(self):
        return [SimpleNamespace(cdr_id="cdr1"), SimpleNamespace(cdr_id="cdr2")]


def test_groups_series_across_cdrs_sorted_by_date(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([
        _res("cdr1", [_obs("pat-A", "c1", 5.0, "2026-08-01"),
                      _obs("pat-A", "c1", 6.0, "2026-08-03"),
                      _obs("pat-A", "c2", 120, "2026-08-02", "mmHg", "BP")]),
        _res("cdr2", [_obs("pat-A", "c1", 5.5, "2026-09-01")]),
    ]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna", "birth_year": "1980"})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A",
                                  ["cdr1", "cdr2"], FakeReg())
    assert out["total_points"] == 4
    assert out["name"] == "Anna" and out["birth_year"] == "1980"
    s = {x["code"]: x for x in out["series"]}
    assert set(s) == {"c1", "c2"}          # keyed by concept guid, not LOINC
    assert s["c1"]["count"] == 3
    assert s["c1"]["unit"] == "mmol/L"
    assert s["c1"]["latest"] == "2026-09-01"
    assert [p["value"] for p in s["c1"]["points"]] == [5.0, 6.0, 5.5]
    assert {p["cdr_id"] for p in s["c1"]["points"]} == {"cdr1", "cdr2"}
    assert out["blocked"] is False and out["exposure"] is False


def test_series_keyed_by_concept_guid_labelled_by_loinc(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([
        _res("cdr1", [_obs("pat-A", "d83a8dac-concept", 5.0, "2026-08-01",
                           label="CGM mean glucose")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A", ["cdr1"], FakeReg())
    s = out["series"][0]
    assert s["code"] == "d83a8dac-concept"        # concept guid, not "L-…"
    assert s["label"] == "CGM mean glucose"       # LOINC display


def test_foreign_patient_rows_are_dropped(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([
        _res("cdr1", [_obs("pat-A", "c1", 5.0, "2026-08-01"),
                      _obs("pat-OTHER", "c1", 9.9, "2026-08-01")]),
    ]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A", ["cdr1"], FakeReg())
    assert out["total_points"] == 1


def test_non_admin_blocked_clinic_is_filtered(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([_res("cdr1", [
        _obs("pat-A", "c1", 5.0, "2026-08-01", org="clinicA"),
        _obs("pat-A", "c1", 6.0, "2026-08-02", org="clinicB"),   # blocked
    ])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna"})
    out = pd.build_patient_detail(
        {"is_su_admin": False,
         "affiliations": [{"care_unit_guid": "cu-1", "role": "nurse"}]},
        "pat-A", ["cdr1"], FakeReg(), blocks=[_block("clinicB")])
    assert out["block_present"] is True
    assert out["filtered_count"] == 1
    assert out["blocked"] is True
    assert out["exposure"] is False
    # Only the un-blocked clinic's observation survives.
    assert out["total_points"] == 1
    assert [p["value"] for p in out["series"][0]["points"]] == [5.0]


def test_admin_blocked_clinic_exposed_and_flagged(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([_res("cdr1", [
        _obs("pat-A", "c1", 5.0, "2026-08-01", org="clinicA"),
        _obs("pat-A", "c1", 6.0, "2026-08-02", org="clinicB"),
    ])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna"})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A", ["cdr1"],
                                  FakeReg(), blocks=[_block("clinicB")])
    assert out["blocked"] is False
    assert out["exposure"] is True          # break-glass → route logs it
    assert out["exposed_orgs"] == ["clinicB"]
    assert out["total_points"] == 2         # admin sees both clinics


def test_caregiver_only_block_shows_data_with_banner(monkeypatch):
    monkeypatch.setattr(pd, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obs("pat-A", "c1", 5.0, "2026-08-01", org="clinicA")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna"})
    caregiver_block = type("B", (), {"is_active": True,
                                     "source_scope_type": "caregiver",
                                     "source_scope_id": "cg-1", "guid": "b1"})()
    out = pd.build_patient_detail(
        {"is_su_admin": False,
         "affiliations": [{"care_unit_guid": "cu-1", "role": "nurse"}]},
        "pat-A", ["cdr1"], FakeReg(), blocks=[caregiver_block])
    # Caregiver-scope blocks are v1-out-of-scope for filtering: data is shown,
    # but the metadata banner still fires (block_present).
    assert out["block_present"] is True
    assert out["blocked"] is False
    assert out["total_points"] == 1


def test_ips_unavailable_hides_for_non_admin(monkeypatch):
    called = {"fanout": False}

    def _f(*a, **k):
        called["fanout"] = True
        return _resp([])
    monkeypatch.setattr(pd, "fanout", _f)
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna"})
    out = pd.build_patient_detail(
        {"is_su_admin": False,
         "affiliations": [{"care_unit_guid": "cu-1", "role": "nurse"}]},
        "pat-A", ["cdr1"], FakeReg(), blocks=[], ips_unavailable=True)
    assert out["blocked"] is True
    assert out["fanout_mode"] == "unavailable"
    assert called["fanout"] is False        # hard outage short-circuits the read
