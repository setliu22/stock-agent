#!/bin/zsh

set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
REFRESHER="$PROJECT_ROOT/scripts/refresh_stock_agent.zsh"

BLUE=$'\033[1;34m'
GREEN=$'\033[1;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[1;31m'
RESET=$'\033[0m'

pause_and_exit() {
    local status="$1"
    print
    read -k 1 "?Press any key to close."
    print
    exit "$status"
}

clear
print "${BLUE}Stock Agent Local Rebuilder${RESET}"
print
print "Project folder:"
print "  $PROJECT_ROOT"
print

if [[ ! -f "$REFRESHER" ]]; then
    print "${RED}The Stock Agent local rebuild helper is missing.${RESET}"
    pause_and_exit 1
fi

print "${GREEN}No GitHub or Git operations will be performed.${RESET}"
print
print "Using the files currently in this local project folder."
print "Refreshing Python packages, running tests, rebuilding, and opening the app."
print
exec /bin/zsh "$REFRESHER"
