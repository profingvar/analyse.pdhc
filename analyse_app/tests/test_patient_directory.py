"""#579/item-3: parse the VERIFIED ips shapes.

ips ``/api/v1/clinics/<g>/patients`` returns a flat JSON array of
PatientIndex.to_dict() — ``{guid, family_name, given_name, birth_date, ...}``
(confirmed against the live ips.pdhc source). The FHIR ``/Patient/<g>``
endpoint returns ``name:[{family, given[]}]`` + ``birthDate``.
"""
from types import SimpleNamespace

import app.services.patient_directory as pdir


def _fake_response(status, payload):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def test_list_clinic_patients_parses_flat_patientindex(monkeypatch):
    payload = [
        {"guid": "p-1", "family_name": "Lindberg", "given_name": "Olof",
         "birth_date": "1948-09-18", "is_active": True},
        {"guid": "p-2", "family_name": "Ek", "given_name": "Sara",
         "birth_date": None, "is_active": True},
    ]
    monkeypatch.setenv("IPS_BASE_URL", "https://ips.pdhc.se")
    monkeypatch.setattr(pdir.requests, "get",
                        lambda *a, **k: _fake_response(200, payload))
    rows = pdir.list_clinic_patients("clinic-1", bearer="tok")
    by = {r["patient_guid"]: r for r in rows}
    assert set(by) == {"p-1", "p-2"}
    assert by["p-1"]["name"] == "Olof Lindberg"     # given + family
    assert by["p-1"]["birth_year"] == "1948"
    assert by["p-2"]["name"] == "Sara Ek"
    assert by["p-2"]["birth_year"] is None


def test_get_patient_parses_fhir_humanname(monkeypatch):
    payload = {"resourceType": "Patient",
               "name": [{"family": "Lindberg", "given": ["Olof"]}],
               "birthDate": "1948-09-18"}
    monkeypatch.setenv("IPS_BASE_URL", "https://ips.pdhc.se")
    monkeypatch.setattr(pdir.requests, "get",
                        lambda *a, **k: _fake_response(200, payload))
    out = pdir.get_patient("p-1", bearer="tok")
    assert out["name"] == "Olof Lindberg"
    assert out["birth_year"] == "1948"
