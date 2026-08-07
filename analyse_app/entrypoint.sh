#!/usr/bin/env sh
# analyse.pdhc container entrypoint. Runs DB migrations to head, then execs
# gunicorn as PID 1. Idempotent: `flask db upgrade` is a no-op when already at
# head, so a container restart is safe.
set -eu

cd /app

echo "[analyse] flask db upgrade"
flask db upgrade

echo "[analyse] starting gunicorn on 0.0.0.0:9110 (published on 127.0.0.1 by compose)"
exec gunicorn \
    --bind 0.0.0.0:9110 \
    --workers 2 \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
