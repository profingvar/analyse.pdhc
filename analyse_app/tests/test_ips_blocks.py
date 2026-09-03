"""#579/item-3: the relationship-free spärr predicates on IpsClient.

Locks the endpoint + parse contract for the two calls the reformed analyse
relies on (verified against ips.pdhc blocks_routes.py):
  - GET /blocks/check?source_clinic_id=<org>  → {"is_blocked": bool, ...}
  - GET /blocks/metadata                       → {"blocked_source_count": int}
"""
from types import SimpleNamespace

from app.services.ips_client import IpsClient


def _resp(status, payload, capture=None):
    def _get(url, params=None, headers=None, timeout=None):
        if capture is not None:
            capture["url"] = url
            capture["params"] = params
        return SimpleNamespace(status_code=status, json=lambda: payload)
    return _get


def _client():
    return IpsClient(token="tok", base_url="https://ips.pdhc.se")


def test_check_source_blocked_true(monkeypatch):
    cap = {}
    monkeypatch.setattr("app.services.ips_client.requests.get",
                        _resp(200, {"is_blocked": True, "blocking_scopes": []}, cap))
    assert _client().check_source_blocked("pat-1", "clinic-9") is True
    assert cap["url"].endswith("/api/v1/patients/pat-1/blocks/check")
    assert cap["params"] == {"source_clinic_id": "clinic-9"}


def test_check_source_blocked_false_and_error(monkeypatch):
    monkeypatch.setattr("app.services.ips_client.requests.get",
                        _resp(200, {"is_blocked": False}))
    assert _client().check_source_blocked("pat-1", "clinic-9") is False
    # non-200 → None (caller fails safe)
    monkeypatch.setattr("app.services.ips_client.requests.get", _resp(503, {}))
    assert _client().check_source_blocked("pat-1", "clinic-9") is None


def test_patient_has_block_counts(monkeypatch):
    monkeypatch.setattr("app.services.ips_client.requests.get",
                        _resp(200, {"blocked_source_count": 2}))
    assert _client().patient_has_block("pat-1") is True
    monkeypatch.setattr("app.services.ips_client.requests.get",
                        _resp(200, {"blocked_source_count": 0}))
    assert _client().patient_has_block("pat-1") is False
    monkeypatch.setattr("app.services.ips_client.requests.get", _resp(500, {}))
    assert _client().patient_has_block("pat-1") is None
