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


# --- #579: per-patient detail + admin spärr-log ----------------------------

def _obsc(patient, code, date, org=None):
    o = {"resourceType": "Observation",
         "subject": {"reference": f"Patient/{patient}"},
         "code": {"coding": [
             {"code": "L-" + code, "display": code,
              "system": "https://termbank.pdhc.se/CodeSystem/loinc"},
             {"code": code, "display": code,
              "system": "https://plan.pdhc.se/Concept"}]},
         "valueQuantity": {"value": 5.0, "unit": "mmol/L"},
         "effectiveDateTime": date}
    if org is not None:
        o["meta"] = {"security": [{"code": "org_guid", "display": org,
                                   "system": "https://cdr.pdhc.se/CodeSystem/org"}]}
    return o


# Patient guids are UUIDs platform-wide (Rule 18); the audit column is UUID.
# Use uuid4-shaped values with hex letters — an all-digit guid gets NUMERIC
# affinity coerced to a float by SQLite's dynamic typing (prod Postgres has a
# real uuid column and is unaffected).
_PAT_X = "1a111111-1b11-4c11-8d11-1e1111111111"
_PAT_Y = "2a222222-2b22-4c22-8d22-2e2222222222"


def test_patient_detail_route_admin_shape(app, monkeypatch):
    import app.analyse.patient_detail as pd
    monkeypatch.setattr(pd, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obsc(_PAT_X, "c1", "2026-01-01")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "X", "birth_year": "1990"})
    monkeypatch.setattr("app.services.ips_client.IpsClient.fetch_active_blocks",
                        lambda self, g: [])
    r = app.test_client().get(f"/api/patient/{_PAT_X}?cdr_ids=cdr1")
    assert r.status_code == 200
    j = r.get_json()
    assert j["patient_guid"] == _PAT_X
    assert j["is_admin"] is True
    assert j["total_points"] == 1
    assert j["series"][0]["code"] == "c1"


def test_patient_detail_admin_exposure_is_logged_and_visible(app, monkeypatch):
    import app.analyse.patient_detail as pd
    monkeypatch.setattr(pd, "fanout",
                        lambda *a, **k: _resp([_res("cdr1", [_obsc(_PAT_Y, "c1", "2026-01-01", org="clinicX")])]))
    monkeypatch.setattr("app.services.patient_directory.get_patient",
                        lambda g, bearer=None: {"name": "Y"})
    # /blocks/check says clinicX (the producing clinic) is blocked for this patient.
    monkeypatch.setattr("app.services.ips_client.IpsClient.check_source_blocked",
                        lambda self, p, org: org == "clinicX")
    c = app.test_client()
    r = c.get(f"/api/patient/{_PAT_Y}?cdr_ids=cdr1")
    assert r.status_code == 200
    assert r.get_json()["exposure"] is True
    # A sparr_lift_exposure audit row was written for this patient …
    with app.app_context():
        from app.models import AnalyseAudit
        rows = AnalyseAudit.query.filter_by(event_type="sparr_lift_exposure").all()
        assert any(x.patient_guid == _PAT_Y for x in rows)
    # … and the admin spärr-log surfaces it.
    log = c.get("/api/admin/sparr-log").get_json()
    assert any(e["event_type"] == "sparr_lift_exposure"
               and e["patient_guid"] == _PAT_Y for e in log["events"])


def test_sparr_log_route_admin_ok(app):
    r = app.test_client().get("/api/admin/sparr-log")
    assert r.status_code == 200
    assert "events" in r.get_json() and "count" in r.get_json()


def test_sparr_log_denies_non_admin(app, monkeypatch):
    from app.services import role_guards as rg
    monkeypatch.setattr(rg, "_is_admin", lambda: False)
    monkeypatch.setattr(rg, "_roles", lambda: {"nurse"})
    r = app.test_client().get("/api/admin/sparr-log")
    assert r.status_code == 403
