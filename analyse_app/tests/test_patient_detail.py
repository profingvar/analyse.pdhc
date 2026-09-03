"""Unit tests for the #579 per-patient clinical detail assembler."""
from types import SimpleNamespace

import app.analyse.patient_detail as pd


def _res(cdr_id, obs_list):
    return SimpleNamespace(cdr_id=cdr_id, ok=True, status_code=200,
                           body={"entry": [{"resource": o} for o in obs_list]})


def _resp(results, mode="complete"):
    return SimpleNamespace(mode=mode, results=results, succeeded=[], failed=[])


def _obs(patient, code, val, date, unit="mmol/L", label=None):
    return {"resourceType": "Observation",
            "subject": {"reference": f"Patient/{patient}"},
            "code": {"coding": [{"code": code, "display": label or code}]},
            "valueQuantity": {"value": val, "unit": unit},
            "effectiveDateTime": date}


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
    assert set(s) == {"c1", "c2"}
    assert s["c1"]["count"] == 3
    assert s["c1"]["unit"] == "mmol/L"
    assert s["c1"]["latest"] == "2026-09-01"
    # points aggregated across CDRs and sorted oldest→newest
    assert [p["value"] for p in s["c1"]["points"]] == [5.0, 6.0, 5.5]
    assert {p["cdr_id"] for p in s["c1"]["points"]} == {"cdr1", "cdr2"}
    assert out["blocked"] is False and out["exposure"] is False


def test_foreign_patient_rows_are_dropped(monkeypatch):
    monkeypatch.setattr(pd, "fanout", lambda *a, **k: _resp([
        _res("cdr1", [_obs("pat-A", "c1", 5.0, "2026-08-01"),
                      _obs("pat-OTHER", "c1", 9.9, "2026-08-01")]),
    ]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A", ["cdr1"], FakeReg())
    assert out["total_points"] == 1


def test_non_admin_block_hides_data(monkeypatch):
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
        "pat-A", ["cdr1"], FakeReg(), blocks=[object()])
    assert out["blocked"] is True
    assert out["series"] == [] and out["total_points"] == 0
    assert out["exposure"] is False
    assert out["fanout_mode"] == "hidden"
    # spärr must short-circuit the CDR read entirely for a blocked non-admin.
    assert called["fanout"] is False
    # demographics still shown (metadata banner needs a name).
    assert out["name"] == "Anna"


def test_admin_block_exposes_and_flags(monkeypatch):
    monkeypatch.setattr(pd, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obs("pat-A", "c1", 5.0, "2026-08-01")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Anna"})
    out = pd.build_patient_detail({"is_su_admin": True}, "pat-A", ["cdr1"],
                                  FakeReg(), blocks=[object()])
    assert out["blocked"] is False
    assert out["exposure"] is True          # break-glass → route logs it
    assert out["block_present"] is True
    assert out["total_points"] == 1
