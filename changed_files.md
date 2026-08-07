# changed_files.md — analyse.pdhc (append-only, Rule 17)

## 2026-08-07 — #538 scaffold + #539 port (new service, all files created)

### Bookkeeping / root
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/readme.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/progress.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/newtask.txt
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/changed_files.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/start.sh
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/docs/technical.md
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/docs/user_manual.md

### Container / packaging
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/Dockerfile
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/docker-compose.yml
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/entrypoint.sh
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/wsgi.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/requirements.txt
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.env.example
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.dockerignore
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/.gitignore

### App package
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/extensions.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/version.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/auth.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/models/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/models/audit.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/audit.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/ips_client.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/role_guards.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/services/session_headers.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/federation.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/aggregations.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/cohort.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/observations_search.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/stats.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/canonical.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/analyse/openehr.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/api/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/api/health.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/auth.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/researcher.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/routes/views.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/app/templates/researcher_workspace.html

### Migrations
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/alembic.ini
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/env.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/script.py.mako
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/migrations/versions/0001_initial.py

### Tests
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/__init__.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/conftest.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_health.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_researcher_flow.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_analyse_aux.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_auth_gate.py
- /Users/martiningvar/T7_sidewinder/analyse.pdhc/analyse_app/tests/test_sso_web.py
