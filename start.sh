#!/usr/bin/env bash
# analyse.pdhc — single entry point (CLAUDE.md §6, Rule 16). Builds + brings
# up ONLY this service's compose project, runs migrations (via the container
# entrypoint), probes /healthz on loopback. Loopback-only deploy. Own ports
# only: 9110 (app) / 9111 (db). Never touches sibling services (Rule 22).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
APP_DIR="$PROJECT_DIR/analyse_app"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: $APP_DIR/.env not found (copy analyse_app/.env.example)" >&2
    exit 1
fi
set -a; . "$APP_DIR/.env"; set +a

APP_PORT="${APP_PORT:-9110}"
DB_PORT="${DB_PORT:-9111}"

# Non-interactive ssh has a minimal PATH on the macmini (CLAUDE.md §8.1).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Docker context (CLAUDE.md §8.2). Never set DOCKER_HOST manually. Ensure
# Colima is up WITHOUT ever stopping/deleting the shared VM (§8.4) — a stop
# or delete takes down every sibling service on the box.
unset DOCKER_HOST || true
docker context use colima >/dev/null 2>&1 || true
if ! docker info >/dev/null 2>&1; then
    if command -v colima >/dev/null 2>&1 && ! colima status >/dev/null 2>&1; then
        echo "[analyse] colima down — starting it (never stop/delete the shared VM)"
        colima start
    fi
fi
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker not responding. Start colima or check context." >&2
    exit 1
fi

# docker compose v2 plugin vs docker-compose v1 (CLAUDE.md §8.3).
DC="docker compose"
command -v docker-compose >/dev/null 2>&1 && DC="docker-compose"

cd "$APP_DIR"

# Belt-and-braces: free ONLY our own host ports before compose binds them.
# Never touch ports outside the 9110/9111 block (Rule 22).
for p in "$APP_PORT" "$DB_PORT"; do
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti tcp:"$p" 2>/dev/null || true)"
        if [ -n "$pids" ]; then
            echo "[analyse] freeing own port $p (pids: $pids)"
            echo "$pids" | xargs -r kill 2>/dev/null || true
        fi
    fi
done

# Pin the compose project so release swaps don't spawn a parallel project
# (CLAUDE.md §6.3). COMPOSE_PROJECT_NAME comes from .env (analyse_pdhc).
echo "[analyse] $DC up -d --build (db + app on 127.0.0.1:$APP_PORT)"
$DC up -d --build

echo "[analyse] waiting for db healthcheck"
for i in $(seq 1 30); do
    state="$(docker inspect analyse_pdhc_db --format '{{.State.Health.Status}}' 2>/dev/null || echo starting)"
    if [ "$state" = "healthy" ]; then
        echo "[analyse] db healthy on attempt $i"
        break
    fi
    sleep 1
done

# Migrations run inside the container entrypoint (flask db upgrade before
# gunicorn execs). Bounded-wait smoke test on /healthz.
echo "[analyse] waiting for http://127.0.0.1:$APP_PORT/healthz"
for i in $(seq 1 30); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
        "http://127.0.0.1:$APP_PORT/healthz" 2>/dev/null || echo 000)"
    if [ "$code" = "200" ]; then
        echo "[analyse] healthy (attempt $i)"
        exit 0
    fi
    sleep 2
done

echo "[analyse] ERROR: /healthz never reached 200 after 60s" >&2
$DC logs app --tail 40 >&2 || true
exit 1
