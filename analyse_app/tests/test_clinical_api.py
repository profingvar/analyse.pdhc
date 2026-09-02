"""Route tests for the #578 clinical patient-list API."""
from types import SimpleNamespace


def _res(cdr_id, obs_list):
    return SimpleNamespace(cdr_id=cdr_id, ok=True, status_code=200,
                           body={"entry": [{"resource": o} for o in obs_list]})


def _resp(results, mode="complete"):
    return SimpleNamespace(mode=mode, results=results, succeeded=[], failed=[])


def _obs(patient, date):
    return {"resourceType": "Observation",
            "subject": {"reference": f"Patient/{patient}"},
            "effectiveDateTime": date}


def test_cdrs_route_ok_for_admin(app):
    # AUTH_MODE=off -> dev SU (admin) blob, which passes clinical_required.
    r = app.test_client().get("/api/cdrs")
    assert r.status_code == 200
    assert "cdrs" in r.get_json()


def test_patients_route_admin_shape(app, monkeypatch):
    import app.analyse.patient_list as pl
    monkeypatch.setattr(pl, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obs("pat-X", "2026-01-01")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "X", "birth_year": "1990"})
    r = app.test_client().get("/api/patients?cdr_ids=cdr1")
    assert r.status_code == 200
    j = r.get_json()
    assert j["is_admin"] is True
    assert j["count"] == 1
    assert j["patients"][0]["patient_guid"] == "pat-X"


def test_clinical_required_denies_researcher_only(app, monkeypatch):
    from app.services import role_guards as rg
    monkeypatch.setattr(rg, "_is_admin", lambda: False)
    monkeypatch.setattr(rg, "_roles", lambda: {"researcher"})
    r = app.test_client().get("/api/cdrs")
    assert r.status_code == 403


def test_clinical_required_allows_nurse(app, monkeypatch):
    from app.services import role_guards as rg
    monkeypatch.setattr(rg, "_is_admin", lambda: False)
    monkeypatch.setattr(rg, "_roles", lambda: {"nurse"})
    r = app.test_client().get("/api/cdrs")
    assert r.status_code == 200
