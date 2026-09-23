"""analyse.pdhc — the GROUP / population + federated-events service.

The extracted group half of the old dashboard (docs/470_scoping.md). It hosts
the researcher/cohort engine and the gateway/monitor-facing federated
endpoints (``/api/v1/observations`` #291; ``/api/v1/stats`` /
``/api/v1/canonical`` / ``/api/v1/openehr`` #292). Gated on the ANALYSIS phase
(NOT care-delivery — that is cd-assist). Federates its reads across CDR2–6,
presenting ``ANALYSE_PDHC_SERVICE_KEY`` as its CDR read identity.

The individual / point-of-care half (nurse + charts) lives in cd-assist.pdhc —
none of it is here.
"""
import os
import logging
import re

from flask import Flask

from app.extensions import db, migrate


def _parse_cdr_endpoints(raw_env: str) -> list[dict]:
    """Parse the comma-separated ``CDR_ENDPOINTS`` env into the list-of-dicts
    shape the federation expects. Mirrors dashboard.pdhc's create_app.

    Each item is either a plain URL (``https://cdr2.pdhc.se`` — cdr_id pulled
    from the ``cdrN.pdhc.se`` hostname) or ``<id>=<url>`` (explicit id, for
    loopback/internal URLs that lack a public hostname, e.g.
    ``cdr2=http://127.0.0.1:9146``)."""
    out: list[dict] = []
    for chunk in (raw_env or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk and not chunk.startswith("http"):
            cdr_id, _, url = chunk.partition("=")
            cdr_id = cdr_id.strip()
            url = url.strip().rstrip("/")
        else:
            url = chunk.rstrip("/")
            m = re.search(r"cdr(\d+)\.pdhc\.se", url)
            cdr_id = f"cdr{m.group(1)}" if m else url
        if not url or not cdr_id:
            continue
        out.append({"cdr_id": cdr_id, "base_url": url})
    return out


def create_app(config=None) -> Flask:
    app = Flask(__name__)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY")
        or os.environ.get("FLASK_SECRET_KEY")
        or "dev",
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        AUTH_MODE=os.environ.get("AUTH_MODE", "off"),
    )
    if config:
        app.config.update(config)

    app.config["SQLALCHEMY_DATABASE_URI"] = (
        app.config.get("DATABASE_URL") or "sqlite:///:memory:"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # --- SSO (analysis-phase bearer validation + web login) ---------------
    app.config.setdefault("SSO_BASE_URL", os.environ.get("SSO_BASE_URL", ""))
    app.config.setdefault("SSO_CLIENT_ID", os.environ.get("SSO_CLIENT_ID", ""))
    app.config.setdefault(
        "SSO_CLIENT_SECRET", os.environ.get("SSO_CLIENT_SECRET", ""))
    app.config.setdefault(
        "SSO_CALLBACK_URL", os.environ.get("SSO_CALLBACK_URL", ""))

    # --- Inbound service-key callers (KNOWN_SERVICES) ---------------------
    # gateway.pdhc (#291) + monitor.pdhc (#292) call the federated endpoints
    # service-to-service; each presents its OWN key.
    app.config.setdefault(
        "GATEWAY_PDHC_SERVICE_KEY",
        os.environ.get("GATEWAY_PDHC_SERVICE_KEY", ""))
    app.config.setdefault(
        "MONITOR_PDHC_SERVICE_KEY",
        os.environ.get("MONITOR_PDHC_SERVICE_KEY", ""))

    # --- analyse's OWN outbound read identity to CDR2–6 -------------------
    app.config.setdefault(
        "ANALYSE_PDHC_SERVICE_KEY",
        os.environ.get("ANALYSE_PDHC_SERVICE_KEY", ""))

    # --- CDR federation ---------------------------------------------------
    # CDR_ENDPOINTS may be supplied by config (tests pass a list of dicts) or
    # parsed from the comma-separated env var.
    if "CDR_ENDPOINTS" not in app.config:
        app.config["CDR_ENDPOINTS"] = _parse_cdr_endpoints(
            os.environ.get("CDR_ENDPOINTS", ""))
    try:
        app.config.setdefault(
            "CDR_FANOUT_TIMEOUT",
            float(os.environ.get("CDR_FANOUT_TIMEOUT", "15")))
    except ValueError:
        app.config.setdefault("CDR_FANOUT_TIMEOUT", 15.0)

    # --- Consent join (ips.pdhc analysis-filter, research reads) ----------
    app.config.setdefault("IPS_BASE_URL", os.environ.get("IPS_BASE_URL", ""))

    db.init_app(app)
    # Import models so Alembic autogenerate + create_all see every table.
    from app import models  # noqa: F401
    migrate.init_app(app, db)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from app.auth import register_cli, install_request_loader
    register_cli(app)
    install_request_loader(app)

    # Blueprints.
    from app.api.health import bp as health_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.views import bp as views_bp
    from app.routes.researcher import (
        bp as researcher_bp,
        register_export_audit_cli,
    )
    # #291 — gateway-facing federated observations search.
    from app.analyse.observations_search import bp as observations_search_bp
    # #292 — federated auxiliary endpoints (row-count stats, canonical-table
    # query, openEHR composition search).
    from app.analyse.stats import bp as analyse_stats_bp
    from app.analyse.canonical import bp as analyse_canonical_bp
    from app.analyse.openehr import bp as analyse_openehr_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(researcher_bp)
    app.register_blueprint(observations_search_bp)
    app.register_blueprint(analyse_stats_bp)
    app.register_blueprint(analyse_canonical_bp)
    app.register_blueprint(analyse_openehr_bp)

    register_export_audit_cli(app)

    return app
