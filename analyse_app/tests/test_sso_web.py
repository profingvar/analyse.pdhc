"""SSO web login flow (analysis-phase gated).

Exercises app/routes/auth.py (login/callback/logout) + the session-capable
global gate in app/auth.py. validate_sso_token is patched at the module where
it is used (app.routes.auth for /callback; app.auth for the gate
re-validation). No real socket is ever opened.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_app


_GOOD_BLOB = {
    "user_guid": "22222222-2222-2222-2222-222222222222",
    "email": "researcher@clinic",
    "user_type": "professional",
    "is_su_admin": True,
    "session_phases": ["analysis"],
    "affiliations": [],
    "organization_ids": [],
    "session_id": "sess-1",
}

_NO_ANALYSIS_BLOB = {
    "user_guid": "33333333-3333-3333-3333-333333333333",
    "user_type": "professional",
    "is_su_admin": False,
    "session_phases": ["care"],
    "affiliations": [],
}


@pytest.fixture
def sso_app():
    return make_app(AUTH_MODE="sso", SSO_BASE_URL="https://sso.pdhc.se",
                    SSO_CLIENT_ID="analyse", SSO_CLIENT_SECRET="secret",
                    SSO_CALLBACK_URL="https://analyse.pdhc.se/auth/callback")


@pytest.fixture
def sso_client(sso_app):
    return sso_app.test_client()


# --- /auth/login ------------------------------------------------------------

def test_login_off_mode_goes_to_landing():
    r = make_app().test_client().get("/auth/login")
    assert r.status_code == 302
    assert r.location.endswith("/")


def test_login_sso_mode_redirects_to_sso_with_state(sso_client):
    r = sso_client.get("/auth/login")
    assert r.status_code == 302
    assert r.location.startswith("https://sso.pdhc.se/login")
    assert "state=" in r.location
    with sso_client.session_transaction() as sess:
        assert sess.get("sso_state")


# --- /auth/callback ---------------------------------------------------------

def test_callback_valid_token_stores_session_and_redirects(sso_client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.validate_sso_token", lambda t: _GOOD_BLOB)
    with sso_client.session_transaction() as sess:
        sess["sso_state"] = "st-1"
    r = sso_client.get("/auth/callback?token=tok-abc&state=st-1")
    assert r.status_code == 302
    with sso_client.session_transaction() as sess:
        assert sess.get("sso_token") == "tok-abc"


def test_callback_bad_state_back_to_login(sso_client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.validate_sso_token", lambda t: _GOOD_BLOB)
    with sso_client.session_transaction() as sess:
        sess["sso_state"] = "expected"
    r = sso_client.get("/auth/callback?token=tok&state=WRONG")
    assert r.status_code == 302
    assert "/auth/login" in r.location
    with sso_client.session_transaction() as sess:
        assert "sso_token" not in sess


def test_callback_invalid_token_back_to_login(sso_client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.validate_sso_token", lambda t: None)
    with sso_client.session_transaction() as sess:
        sess["sso_state"] = "st-1"
    r = sso_client.get("/auth/callback?token=bad&state=st-1")
    assert r.status_code == 302
    assert "/auth/login" in r.location


def test_callback_no_analysis_denied(sso_client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.validate_sso_token",
                        lambda t: _NO_ANALYSIS_BLOB)
    with sso_client.session_transaction() as sess:
        sess["sso_state"] = "st-1"
    r = sso_client.get("/auth/callback?token=tok&state=st-1")
    assert r.status_code == 302
    assert "/auth/login" in r.location
    with sso_client.session_transaction() as sess:
        assert "sso_token" not in sess


# --- gate re-validation: landing shell --------------------------------------

def test_landing_redirects_to_login_when_no_session(sso_client):
    r = sso_client.get("/")
    assert r.status_code == 302
    assert "/auth/login" in r.location


def test_landing_renders_with_valid_session(sso_client, monkeypatch):
    # #578: the landing is now the org-scoped "choose patient" list; the
    # cohort/research workspace moved to /researcher.
    monkeypatch.setattr("app.auth.validate_sso_token", lambda t: _GOOD_BLOB)
    with sso_client.session_transaction() as sess:
        sess["sso_token"] = "tok-live"
    r = sso_client.get("/")
    assert r.status_code == 200
    assert b"choose patient" in r.data


def test_researcher_workspace_still_available(sso_client, monkeypatch):
    monkeypatch.setattr("app.auth.validate_sso_token", lambda t: _GOOD_BLOB)
    with sso_client.session_transaction() as sess:
        sess["sso_token"] = "tok-live"
    r = sso_client.get("/researcher")
    assert r.status_code == 200
    assert b"Researcher workspace" in r.data


# --- /auth/logout -----------------------------------------------------------

def test_logout_clears_session(sso_client):
    with sso_client.session_transaction() as sess:
        sess["sso_token"] = "tok-live"
    r = sso_client.get("/auth/logout")
    assert r.status_code == 302
    with sso_client.session_transaction() as sess:
        assert "sso_token" not in sess
