#!/usr/bin/env bash
# run.sh — Astrolift local dev command center.
# Usage: ./run.sh [command] [args]
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "  ${CYAN}→${NC} $*"; }
ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${NC} $*"; }
die()     { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }
banner()  { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/backend/config/local.env"
COMPOSE="docker compose -f $REPO_DIR/docker/docker-compose.yaml"
API_CONTAINER="astrolift-local"
UI_CONTAINER="ui"
API_PORT=8000
UI_PORT=3000

# Auto-bootstrap if env file is missing
if [[ ! -f "$ENV_FILE" ]]; then
    warn "backend/config/local.env not found — running bootstrap..."
    bash "$REPO_DIR/bootstrap.sh"
    echo ""
fi

CMD="${1:-up}"
shift 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────

_require_running() {
    docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null | grep -q true \
        || die "$1 is not running. Start it with: ./run.sh up"
}

_urls() {
    echo ""
    echo -e "  Frontend         ${CYAN}http://localhost:$UI_PORT${NC}"
    echo -e "  Backend          ${CYAN}http://localhost:$API_PORT/app/${NC}"
    echo -e "  Admin            ${CYAN}http://localhost:$API_PORT/app/admin/${NC}"
    echo -e "  GraphQL          ${CYAN}http://localhost:$API_PORT/app/gql/config/${NC}"
    echo -e "  Health           ${CYAN}http://localhost:$API_PORT/health/${NC}"
    echo -e "  Temporal UI      ${CYAN}http://localhost:8233${NC}"
    echo -e "  Mailpit          ${CYAN}http://localhost:8025${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────

case "$CMD" in
    up)
        banner "Starting Astrolift stack"
        $COMPOSE up -d
        ok "stack started"
        _urls
        ;;
    down)
        banner "Stopping Astrolift stack"
        $COMPOSE down
        ok "stack stopped"
        ;;
    build)
        banner "Rebuilding Astrolift stack"
        $COMPOSE up -d --build
        ok "stack rebuilt"
        _urls
        ;;
    restart)
        $COMPOSE restart "${1:-$API_CONTAINER}"
        ;;
    logs)
        $COMPOSE logs -f "${1:-$API_CONTAINER}"
        ;;
    shell)
        _require_running "$API_CONTAINER"
        $COMPOSE exec "$API_CONTAINER" bash
        ;;
    shell-ui)
        _require_running "$UI_CONTAINER"
        $COMPOSE exec "$UI_CONTAINER" sh
        ;;
    manage)
        _require_running "$API_CONTAINER"
        $COMPOSE exec "$API_CONTAINER" python manage.py "$@"
        ;;
    migrate)
        _require_running "$API_CONTAINER"
        $COMPOSE exec "$API_CONTAINER" python manage.py migrate
        ;;
    seed)
        _require_running "$API_CONTAINER"
        $COMPOSE exec "$API_CONTAINER" python manage.py seed_dev_identity
        ;;
    test)
        _require_running "$API_CONTAINER"
        $COMPOSE exec "$API_CONTAINER" pytest -x "$@"
        ;;
    ps)
        $COMPOSE ps
        ;;
    urls)
        _urls
        ;;
    help|--help|-h)
        cat <<EOF
Astrolift — local dev command center

Usage: ./run.sh [command]

Stack:
  up          Start the full stack (default)
  down        Stop the stack
  build       Rebuild + restart
  restart [s] Restart one service (default: astrolift-local)
  ps          Show running services
  urls        Print useful local URLs

Backend:
  logs [s]    Tail logs (default: astrolift-local)
  shell       Bash shell in the backend container
  manage ...  Run a manage.py command
  migrate     Run Django migrations
  seed        Re-seed dev identity (acme/eng/api/dev@local)
  test        Run pytest

Frontend:
  shell-ui    Shell in the UI container

Other:
  help        This help
EOF
        ;;
    *)
        die "unknown command: $CMD (try ./run.sh help)"
        ;;
esac
