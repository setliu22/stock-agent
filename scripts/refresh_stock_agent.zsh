#!/bin/zsh

set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_PATH="$PROJECT_ROOT/Stock Agent.app"
UPDATE_LOG="$PROJECT_ROOT/data/stock_agent_update.log"

mkdir -p "$PROJECT_ROOT/data"
: > "$UPDATE_LOG"

BLUE=$'\033[1;34m'
GREEN=$'\033[1;32m'
RED=$'\033[1;31m'
RESET=$'\033[0m'

step() {
    print
    print "${BLUE}$1${RESET}"
}

fail() {
    print "${RED}✗ $1${RESET}"
    print "See: $UPDATE_LOG"
    print
    read -k 1 "?Press any key to close."
    print
    exit 1
}

clear
print "${BLUE}Stock Agent Native Rebuilder${RESET}"
print
print "Project folder: $PROJECT_ROOT"

step "[1/4] Checking the native toolchain"
command -v swift >/dev/null 2>&1 || fail "Swift was not found. Install the free Xcode app or Command Line Tools."
swift --version 2>&1 | tee -a "$UPDATE_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then fail "Swift could not start."; fi
print "${GREEN}✓ Swift toolchain found${RESET}"

step "[2/4] Running native tests"
swift test --package-path "$PROJECT_ROOT/native" 2>&1 | tee -a "$UPDATE_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then fail "Native tests failed."; fi
print "${GREEN}✓ Native tests passed${RESET}"

step "[3/4] Building and ad-hoc signing Stock Agent.app"
/bin/zsh "$PROJECT_ROOT/scripts/build_native_app.zsh" 2>&1 | tee -a "$UPDATE_LOG"
if [[ ${pipestatus[1]} -ne 0 ]] || [[ ! -x "$APP_PATH/Contents/MacOS/StockAgent" ]]; then
    fail "The native application could not be built."
fi
print "${GREEN}✓ Native app built${RESET}"

step "[4/4] Opening Stock Agent"
/usr/bin/open "$APP_PATH"
print "${GREEN}✓ Update complete${RESET}"
print
print "Done."
print
read -k 1 "?Press any key to close this update window."
print
