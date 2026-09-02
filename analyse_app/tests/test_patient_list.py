"""Unit tests for the #578 org-scoped clinical patient list."""
from types import SimpleNamespace

import app.analyse.patient_list as pl


def _res(cdr_id, obs_list):
    return SimpleNamespace(cdr_id=cdr_id, ok=True, status_code=200,
                           body={"entry": [{"resource": o} for o in obs_list]})


def _resp(results, mode="complete"):
    return SimpleNamespace(mode=mode, results=results, succeeded=[], failed=[])


def _obs(patient, date):
    return {"resourceType": "Observation",
            "subject": {"reference": f"Patient/{patient}"},
            "effectiveDateTime": date}


class FakeReg:
    def all(self):
        return [SimpleNamespace(cdr_id="cdr1"), SimpleNamespace(cdr_id="cdr2")]


def test_non_admin_is_org_scoped_and_aggregates(monkeypatch):
    monkeypatch.setattr(pl, "fanout", lambda *a, **k: _resp([
        _res("cdr1", [_obs("pat-A", "2026-08-01"), _obs("pat-A", "2026-08-05"),
                      _obs("pat-B", "2026-07-01")]),
        _res("cdr2", [_obs("pat-A", "2026-09-02")]),
    ]))
    monkeypatch.setattr(
        "app.services.patient_directory.list_clinic_patients",
        lambda cu, bearer=None: [
            {"patient_guid": "pat-A", "name": "Anna A", "birth_year": "1980"},
            {"patient_guid": "pat-C", "name": "Cecil C", "birth_year": "1975"},
        ])
    blob = {"is_su_admin": False,
            "affiliations": [{"care_unit_guid": "cu-1", "role": "nurse"}]}
    out = pl.build_patient_list(blob, ["cdr1", "cdr2"], FakeReg(),
                               block_checker=lambda g: False)
    rows = {p["patient_guid"]: p for p in out["patients"]}
    # pat-B has CDR data but is NOT in the caller's clinic -> excluded.
    assert set(rows) == {"pat-A", "pat-C"}
    assert rows["pat-A"]["datapoints"] == 3
    assert rows["pat-A"]["latest_date"] == "2026-09-02"
    assert rows["pat-A"]["per_cdr"] == {"cdr1": 2, "cdr2": 1}
    assert rows["pat-A"]["name"] == "Anna A"
    assert rows["pat-C"]["datapoints"] == 0     # in clinic, no CDR data
    assert out["is_admin"] is False


def test_sparr_hides_counters_for_non_admin(monkeypatch):
    monkeypatch.setattr(pl, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obs("pat-A", "2026-08-01")])]))
    monkeypatch.setattr(
        "app.services.patient_directory.list_clinic_patients",
        lambda cu, bearer=None: [{"patient_guid": "pat-A", "name": "Anna", "birth_year": "1980"}])
    blob = {"is_su_admin": False,
            "affiliations": [{"care_unit_guid": "cu-1", "role": "nurse"}]}
    out = pl.build_patient_list(blob, ["cdr1"], FakeReg(), block_checker=lambda g: True)
    p = out["patients"][0]
    assert p["blocked"] is True
    assert p["datapoints"] == 0 and p["latest_date"] is None
    assert out["blocked_count"] == 1


def test_admin_sees_cdr_patients_and_bypasses_sparr(monkeypatch):
    monkeypatch.setattr(pl, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obs("pat-X", "2026-01-01")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "X", "birth_year": "1990"})
    out = pl.build_patient_list({"is_su_admin": True}, ["cdr1"], FakeReg(),
                                block_checker=lambda g: True)
    assert out["is_admin"] is True
    assert [p["patient_guid"] for p in out["patients"]] == ["pat-X"]
    assert out["patients"][0]["datapoints"] == 1   # admin bypasses the block
