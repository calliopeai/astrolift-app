#!/usr/bin/env bash
# bootstrap.sh — First-time local dev setup for Astrolift.
# Safe to re-run; skips steps already done.
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "  ${CYAN}→${NC} $*"; }
ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
warn()    { echo -e "  ${YELLOW}⚠${NC} $*"; }
die()     { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }
banner()  { echo -e "\n${BOLD}${CYAN}▶ $*${NC}"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/backend/config/local.env"
EXAMPLE_ENV="$REPO_DIR/backend/config/example.env"

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║      Astrolift Bootstrap             ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════╝${NC}"

# ── 1. Dependencies ──────────────────────────────────────────────────────────
banner "Checking dependencies"

command -v docker &>/dev/null || die "Docker not found. Install Docker Desktop: https://docs.docker.com/get-docker/"
ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"

docker compose version &>/dev/null || die "docker compose (v2) not found. Upgrade Docker Desktop."
ok "docker compose $(docker compose version --short 2>/dev/null || echo 'v2')"

# ── 2. Environment file ───────────────────────────────────────────────────────
banner "Backend environment file"

if [[ -f "$ENV_FILE" ]]; then
    ok "backend/config/local.env already exists — skipping"
elif [[ -f "$EXAMPLE_ENV" ]]; then
    cp "$EXAMPLE_ENV" "$ENV_FILE"
    ok "Created backend/config/local.env from example.env"
    warn "Open backend/config/local.env and fill in any required values"
else
    warn "backend/config/example.env not found — please create backend/config/local.env manually"
fi

# ── 3. Frontend env ───────────────────────────────────────────────────────────
banner "Frontend environment file"

UI_ENV="$REPO_DIR/frontend/.env"
UI_EXAMPLE="$REPO_DIR/frontend/.env.example"
if [[ -f "$UI_ENV" ]]; then
    ok "frontend/.env already exists — skipping"
elif [[ -f "$UI_EXAMPLE" ]]; then
    cp "$UI_EXAMPLE" "$UI_ENV"
    ok "Created frontend/.env from .env.example"
fi

echo ""
ok "Bootstrap done. Next: ./run.sh up"
echo ""
