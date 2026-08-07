"""Flask extensions — kept separate to avoid circular imports.

``JSONB`` is dialect-aware: real ``JSONB`` on Postgres (production), plain
``JSON`` on SQLite so the hermetic test suite can ``create_all()`` (same
pattern as dashboard.pdhc/app/models and cd_assist_app).
"""
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON, Text
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB


db = SQLAlchemy()
migrate = Migrate()

JSONB = JSON().with_variant(_PG_JSONB(astext_type=Text()), "postgresql")
