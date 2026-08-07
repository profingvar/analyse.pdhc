"""Auth + org-scoping for analyse.pdhc (analysis-phase front door).

analyse.pdhc is the GROUP / population + federated-events half of the old
dashboard. Its front door is the ANALYSIS phase (NOT care-delivery — that is
cd-assist's gate):

    access = SU admin
             OR (professional AND 'analysis' in session_phases
                 (dual-read effective_phases))

AUTH_MODE=off  → loads a dev SU user (Rule 23, local dev only); the dev blob
                 carries session_phases:["analysis"].
AUTH_MODE=sso  → OAuth-style flow against sso.pdhc, mirroring gateway.pdhc /
                 dashboard.pdhc. The bearer is re-validated against sso.pdhc
                 /api/auth/me/service on EVERY request (Rule 11, no blob cache),
                 so an SSO-side logout locks the caller out immediately.

Service-key bypass (KNOWN_SERVICES): gateway.pdhc (#291 — its
``/api/v1/observations`` proxy) and monitor.pdhc (#292 — benchmarks/smoke)
call the analyse-layer federated endpoints service-to-service with
``X-Source-Service`` + ``X-Service-Key``. Each presents its OWN key
(GATEWAY_PDHC_SERVICE_KEY / MONITOR_PDHC_SERVICE_KEY). analyse's own outbound
identity to CDR2–6 is ``ANALYSE_PDHC_SERVICE_KEY`` (used by analyse/federation).

Rule 24: non-admin users are org-scoped to their affiliation care-units
(``scope_org_guids``); each CDR enforces that scope on reads via X-Org-Guids.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Optional

import click
import requests
from flask import current_app, g, request, session, redirect, url_for, abort

from app.models import db, User


# ---------- helpers ----------

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


_DEV_BLOB = {
    "user_guid": "00000000-0000-0000-0000-000000000000",
    "email": "dev@local",
    "display_name": "Dev SU",
    "user_type": "professional",
    "is_su_admin": True,
    "session_phases": ["analysis"],
    "effective_phases": ["analysis"],
    "organization_ids": [],
    "affiliations": [],
    "session_id": None,
}


def _blob_to_user(blob: dict) -> SimpleNamespace:
    """Lightweight wrapper exposing the attributes routes expect."""
    return SimpleNamespace(
        guid=blob.get("user_guid"),
        username=blob.get("email") or blob.get("user_guid"),
        is_admin=bool(blob.get("is_su_admin")),
        is_su=bool(blob.get("is_su_admin")),
        org_ids=scope_org_guids(blob),
        blob=blob,
    )


def _phases(blob: dict) -> list:
    """Reform-canonical phases (M0 #415): prefer session_phases (Option C),
    fall back to the dual-emitted legacy effective_phases for pre-reform
    tokens."""
    return blob.get("session_phases") or blob.get("effective_phases") or []


def scope_org_guids(blob: dict) -> list:
    """Zone-1 read scope (M0 #415): affiliations[].care_unit_guid — the exact
    equivalent of the legacy flat organization_ids semantics — with a dual-read
    fallback to organization_ids for pre-reform tokens. Zone-2 (parent care
    organisation) is deliberately NOT folded in here."""
    if not isinstance(blob, dict):
        return list(getattr(blob, "organization_ids", None) or [])
    affs = blob.get("affiliations") or []
    if affs:
        return [a["care_unit_guid"] for a in affs if a.get("care_unit_guid")]
    return list(blob.get("organization_ids") or [])


def research_project_guids(blob: dict) -> list:
    """Reader-side research scope (M0 #415): the union of
    research_project_guids across the caller's affiliations, order-preserving.
    Intersected against the patient's consented_research_projects by ips's
    analysis-filter — never locally."""
    seen: set = set()
    out: list = []
    for a in (blob.get("affiliations") if isinstance(blob, dict) else None) or []:
        for guid in a.get("research_project_guids") or []:
            if guid not in seen:
                seen.add(guid)
                out.append(guid)
    return out


def has_analysis_access(blob: Optional[dict]) -> bool:
    """analyse.pdhc's front door (mirrors dashboard.pdhc analysis gate).

    Access = SU admin, OR a professional whose active session carries the
    'analysis' phase (session_phases, dual-read effective_phases)."""
    if not blob:
        return False
    if blob.get("is_su_admin"):
        return True
    return (
        blob.get("user_type") == "professional"
        and "analysis" in _phases(blob)
    )


# ---------- SSO calls ----------

def validate_sso_token(token: str) -> Optional[dict]:
    """Call sso.pdhc /api/auth/me/service with the bearer + service creds.
    Returns the access blob or None on failure. Mirrors gateway/dashboard."""
    base = current_app.config.get("SSO_BASE_URL", "").rstrip("/")
    cid = current_app.config.get("SSO_CLIENT_ID", "")
    sec = current_app.config.get("SSO_CLIENT_SECRET", "")
    if not (base and cid and sec):
        current_app.logger.error(
            "SSO config missing (BASE_URL/CLIENT_ID/CLIENT_SECRET)")
        return None
    try:
        r = requests.get(
            f"{base}/api/auth/me/service",
            headers={
                "Authorization": f"Bearer {token}",
                "X-SSO-Client-Id": cid,
                "X-SSO-Client-Secret": sec,
            },
            timeout=10,
            verify=True,
        )
        if r.status_code == 200:
            return r.json()
        current_app.logger.warning(
            "SSO validate failed: %s %s", r.status_code, r.text[:200])
        return None
    except requests.RequestException as e:
        current_app.logger.error("SSO validate error: %s", e)
        return None


def initiate_sso_login(next_url: str, state: str) -> str:
    base = current_app.config.get("SSO_BASE_URL", "").rstrip("/")
    cb = current_app.config.get("SSO_CALLBACK_URL", "")
    return f"{base}/login?next={cb}&state={state}"


# ---------- per-request loader ----------

def _upsert_local_user(blob: dict) -> None:
    """Ensure a local ``users`` row exists for the SSO caller."""
    guid = blob.get("user_guid")
    if not guid:
        return
    u = User.query.filter_by(guid=guid).first()
    if not u:
        u = User(
            guid=guid,
            username=blob.get("email") or guid,
            is_admin=bool(blob.get("is_su_admin")),
            is_su=bool(blob.get("is_su_admin")),
        )
        db.session.add(u)
        db.session.commit()


def _public_path(path: str) -> bool:
    return (
        path.startswith("/auth/")
        or path == "/healthz"
        or path == "/api/v1/health"
        or path.startswith("/static/")
    )


# Service-key auth: trusted sibling services may call the analyse-layer
# federated endpoints (observations_search/stats/canonical/openehr) without an
# SSO session. Each presents its OWN key.
#   gateway.pdhc — #291, its /api/v1/observations proxy lands here.
#   monitor.pdhc — #292, benchmarks / smoke / CI.
KNOWN_SERVICES = {
    "monitor.pdhc": "MONITOR_PDHC_SERVICE_KEY",
    "gateway.pdhc": "GATEWAY_PDHC_SERVICE_KEY",
}


def _service_key_outcome(app):
    """None / True / False — same shape as dashboard/cdr's _service_key_outcome."""
    source = request.headers.get("X-Source-Service", "").strip()
    key = request.headers.get("X-Service-Key", "").strip()
    if not source and not key:
        return None
    if not source or not key:
        return False
    cfg_var = KNOWN_SERVICES.get(source)
    if not cfg_var:
        return False
    expected = app.config.get(cfg_var, "")
    if not expected or key != expected:
        return False
    g.source_service = source
    return True


def _service_blob(source_service: str) -> dict:
    """Machine identity for service-key callers (M0 #415).

    Service callers carry NO clinical roles and NO admin bit: they can only
    reach the analyse-layer service endpoints, which gate on ``service_source``
    explicitly (observations_search / stats / canonical / openehr,
    #291/#292). The researcher UI routes (researcher_required) require a real
    operator session with affiliations[]."""
    return {
        "user_guid": f"00000000-0000-0000-0000-service-{source_service[:8]}",
        "email": f"service:{source_service}",
        "display_name": f"service:{source_service}",
        "user_type": "service",
        "is_su_admin": False,
        "session_phases": ["analysis"],
        "organization_ids": [],
        "affiliations": [],
        "service_source": source_service,
    }


def install_request_loader(app):
    """Install a single before_request that resolves the current caller.

    Order: public paths → service-key → AUTH_MODE=off dev-SU → SSO bearer
    re-validation. The analysis-phase gate (``has_analysis_access``) is applied
    to every non-public, non-service request."""

    @app.before_request
    def _loader():  # noqa: ANN202
        if _public_path(request.path):
            return None
        sk = _service_key_outcome(app)
        if sk is True:
            blob = _service_blob(g.source_service)
            g.access_blob = blob
            g.current_user = _blob_to_user(blob)
            return None
        if sk is False:
            from flask import jsonify
            return jsonify({"error": "Invalid service credentials"}), 403
        mode = app.config.get("AUTH_MODE", "off")
        if mode == "off":
            g.access_blob = _DEV_BLOB
            g.current_user = _blob_to_user(_DEV_BLOB)
            return None
        # sso — always re-validate the bearer with sso.pdhc (Rule 11).
        token = session.get("sso_token")
        if not token:
            session["sso_next"] = request.url
            return redirect(url_for("auth.login"))
        blob = validate_sso_token(token)
        if not blob:
            session.clear()
            session["sso_next"] = request.url
            return redirect(url_for("auth.login"))
        session["access_blob"] = blob
        if blob.get("must_change_password"):
            base = app.config.get("SSO_BASE_URL", "").rstrip("/")
            return redirect(f"{base}/change-password")
        if not has_analysis_access(blob):
            abort(403)
        g.access_blob = blob
        g.current_user = _blob_to_user(blob)
        return None


# ---------- org scoping (Rule 24) ----------

def org_guids_for(user) -> list[str]:
    if getattr(user, "is_admin", False):
        return []  # empty = no restriction
    return list(getattr(user, "org_ids", []) or [])


def load_user():  # noqa: D401
    """No-op kept for back-compat with existing route imports."""
    return None


# ---------- CLI: bootstrap SU (Rule 23) ----------

def register_cli(app):
    @app.cli.command("create-su")
    @click.option("--username", required=True)
    @click.option("--password", required=True)
    def create_su(username, password):
        existing = User.query.filter_by(username=username).first()
        if existing:
            existing.is_su = True
            existing.is_admin = True
            existing.password_hash = _hash(password)
        else:
            db.session.add(User(
                username=username,
                password_hash=_hash(password),
                is_su=True, is_admin=True,
            ))
        db.session.commit()
        click.echo(f"SU {username} ready")
