"""Analysis-phase gate + service-key auth (mirror dashboard.pdhc auth)."""
from __future__ import annotations

import pytest

from app.auth import has_analysis_access

from tests.conftest import make_app


# --- has_analysis_access unit -----------------------------------------------

def test_has_analysis_access_su_admin():
    assert has_analysis_access({"is_su_admin": True}) is True


def test_has_analysis_access_professional_with_phase():
    assert has_analysis_access({
        "user_type": "professional", "session_phases": ["analysis"]}) is True


def test_has_analysis_access_dual_read_effective_phases():
    # Pre-reform tokens carry effective_phases, not session_phases.
    assert has_analysis_access({
        "user_type": "professional", "effective_phases": ["analysis"]}) is True


def test_has_analysis_access_professional_without_phase():
    assert has_analysis_access({
        "user_type": "professional", "session_phases": ["care"]}) is False


def test_has_analysis_access_none_and_service():
    assert has_analysis_access(None) is False
    assert has_analysis_access({"user_type": "service"}) is False


# --- service-key gate on the federated endpoints ----------------------------

def test_service_endpoint_denies_without_service_key_in_dev():
    # AUTH_MODE=off gives a dev-SU blob (no service_source); the federated
    # endpoints self-gate on service_source and 401.
    r = make_app().test_client().get("/api/v1/stats")
    assert r.status_code == 401


def test_service_endpoint_bad_credentials_403():
    app = make_app(GATEWAY_PDHC_SERVICE_KEY="right")
    r = app.test_client().get(
        "/api/v1/stats",
        headers={"X-Source-Service": "gateway.pdhc", "X-Service-Key": "wrong"})
    assert r.status_code == 403


# --- SSO gate: no bearer → redirect to login --------------------------------

def test_sso_mode_gated_route_redirects_to_login(monkeypatch):
    app = make_app(AUTH_MODE="sso", SSO_BASE_URL="https://sso.pdhc.se",
                   SSO_CLIENT_ID="analyse", SSO_CLIENT_SECRET="secret")
    r = app.test_client().post("/api/cohort", json={})
    assert r.status_code == 302
    assert "/auth/login" in r.location


def test_sso_mode_invalid_bearer_session_redirects(monkeypatch):
    app = make_app(AUTH_MODE="sso", SSO_BASE_URL="https://sso.pdhc.se",
                   SSO_CLIENT_ID="analyse", SSO_CLIENT_SECRET="secret")
    # A stale session token that no longer validates must bounce to login
    # (per-request revalidation, Rule 11).
    monkeypatch.setattr("app.auth.validate_sso_token", lambda t: None)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["sso_token"] = "stale"
    r = c.post("/api/cohort", json={})
    assert r.status_code == 302
    assert "/auth/login" in r.location


def test_sso_mode_valid_bearer_without_analysis_403(monkeypatch):
    app = make_app(AUTH_MODE="sso", SSO_BASE_URL="https://sso.pdhc.se",
                   SSO_CLIENT_ID="analyse", SSO_CLIENT_SECRET="secret")
    # Authenticates but lacks the analysis phase → 403 on a gated route.
    monkeypatch.setattr("app.auth.validate_sso_token", lambda t: {
        "user_guid": "u1", "user_type": "professional",
        "session_phases": ["care"], "affiliations": []})
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["sso_token"] = "live"
    r = c.get("/api/cohort")
    assert r.status_code == 403
