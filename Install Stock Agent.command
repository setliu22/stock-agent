#!/bin/zsh

set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
APP_PATH="$PROJECT_ROOT/Stock Agent.app"
LOG_DIR="$PROJECT_ROOT/data"
LOG_FILE="$LOG_DIR/stock_agent_gui.log"
INSTALL_LOG="$LOG_DIR/stock_agent_install.log"

mkdir -p "$LOG_DIR"
: > "$INSTALL_LOG"

BLUE=$'\033[1;34m'
GREEN=$'\033[1;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[1;31m'
RESET=$'\033[0m'

step() {
    print
    print "${BLUE}============================================================${RESET}"
    print "${BLUE}$1${RESET}"
    print "${BLUE}============================================================${RESET}"
}

success() { print "${GREEN}✓ $1${RESET}"; }
warning() { print "${YELLOW}! $1${RESET}"; }

fail() {
    print
    print "${RED}✗ $1${RESET}"
    print "${RED}See: $INSTALL_LOG${RESET}"
    print
    read -k 1 "?Press any key to close."
    print
    exit 1
}

find_system_python() {
    local candidate
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            local resolved
            resolved="$(command -v "$candidate" 2>/dev/null || print -r -- "$candidate")"
            if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
                print -r -- "$resolved"
                return 0
            fi
        fi
    done
    return 1
}

clear
print "${BLUE}Stock Agent Timeout and Stop Installer${RESET}"
print
print "Project folder:"
print "  $PROJECT_ROOT"
print
print "Every step is shown here and copied to:"
print "  $INSTALL_LOG"

step "[1/10] Checking project files"
for required in gui.py requirements.txt pyproject.toml pytest.ini stock_agent_launcher.py; do
    [[ -f "$PROJECT_ROOT/$required" ]] || fail "$required was not found."
done
[[ -d "$PROJECT_ROOT/portfolio" ]] || fail "The portfolio package folder was not found."
[[ -d "$PROJECT_ROOT/tests" ]] || fail "The tests folder was not found."
success "Project files found"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    warning ".env was not found. The application will still install, but Groq chat will be disabled."
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    warning "Created .env from .env.example. Add your GROQ_API_KEY later."
else
    success "Existing .env preserved"
fi

step "[2/10] Selecting Python"
SYSTEM_PYTHON="$(find_system_python)"
[[ -n "${SYSTEM_PYTHON:-}" ]] || fail "Python 3.11 or newer was not found. Install Python 3 from python.org and rerun."
print "Using system Python:"
print "  $SYSTEM_PYTHON"
"$SYSTEM_PYTHON" --version 2>&1 | tee -a "$INSTALL_LOG"
success "Compatible Python selected"

step "[3/10] Creating a fresh virtual environment"
rm -rf "$PROJECT_ROOT/.venv"
"$SYSTEM_PYTHON" -m venv "$PROJECT_ROOT/.venv" 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "The virtual environment could not be created."
fi
PYTHON="$PROJECT_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || fail "The new virtual environment has no executable Python."
success "Fresh .venv created"

export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

step "[4/10] Updating packaging tools"
"$PYTHON" -m pip install --upgrade pip setuptools wheel 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "pip, setuptools, or wheel could not be updated."
fi
success "Packaging tools updated"

step "[5/10] Installing requirements"
"$PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt" 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "Dependency installation failed."
fi
success "Requirements installed"

step "[6/10] Installing the local package"
"$PYTHON" -m pip install -e "$PROJECT_ROOT" 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "The local stock-agent package could not be installed."
fi
success "Local package installed"

step "[7/10] Verifying imports"
"$PYTHON" - <<'PYTHON_CHECK' 2>&1 | tee -a "$INSTALL_LOG"
modules = [
    "tkinter",
    "dotenv",
    "pandas",
    "yfinance",
    "langchain_groq",
    "lseg.data",
    "portfolio",
    "portfolio.controller",
    "portfolio.company_resolver",
    "portfolio.lseg_research",
    "portfolio.research_planner",
    "portfolio.research_workflows",
    "portfolio.lseg_capabilities",
]
for module_name in modules:
    __import__(module_name)
    print(f"OK  {module_name}")
print("All required imports succeeded.")
PYTHON_CHECK
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "One or more required Python imports failed."
fi
success "Imports verified"

step "[8/10] Cataloguing the installed LSEG API"
"$PYTHON" -m portfolio.lseg_capabilities --output "$PROJECT_ROOT/data/lseg_capabilities.json" 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "The LSEG capability catalog could not be generated."
fi
success "Executable workflow registry and installed public-callable inventory exported"

step "[9/10] Running automated tests"
print "Running only this project's tests:"
print "  .venv/bin/python -m pytest -c pytest.ini tests"
print
"$PYTHON" -m pytest -c "$PROJECT_ROOT/pytest.ini" "$PROJECT_ROOT/tests" 2>&1 | tee -a "$INSTALL_LOG"
if [[ ${pipestatus[1]} -ne 0 ]]; then
    fail "The automated tests failed."
fi
success "All automated tests passed"

step "[10/10] Building the macOS application"
escape_applescript() {
    print -r -- "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

ESCAPED_PROJECT="$(escape_applescript "$PROJECT_ROOT")"
ESCAPED_PYTHON="$(escape_applescript "$PYTHON")"
ESCAPED_LAUNCHER="$(escape_applescript "$PROJECT_ROOT/stock_agent_launcher.py")"
ESCAPED_LOG="$(escape_applescript "$LOG_FILE")"
TEMP_SCRIPT="$(mktemp -t stock-agent-launcher).applescript"

cat > "$TEMP_SCRIPT" <<APPLESCRIPT
on run
    set projectRoot to "$ESCAPED_PROJECT"
    set pythonPath to "$ESCAPED_PYTHON"
    set launcherPath to "$ESCAPED_LAUNCHER"
    set logPath to "$ESCAPED_LOG"
    set launchCommand to "cd " & quoted form of projectRoot & "; /usr/bin/nohup " & quoted form of pythonPath & " " & quoted form of launcherPath & " >> " & quoted form of logPath & " 2>&1 < /dev/null &"
    do shell script launchCommand
end run
APPLESCRIPT

rm -rf "$APP_PATH"
/usr/bin/osacompile -o "$APP_PATH" "$TEMP_SCRIPT" 2>&1 | tee -a "$INSTALL_LOG"
COMPILE_STATUS=${pipestatus[1]}
rm -f "$TEMP_SCRIPT"
if [[ $COMPILE_STATUS -ne 0 ]] || [[ ! -d "$APP_PATH" ]]; then
    fail "macOS could not create Stock Agent.app."
fi
/usr/bin/xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true
success "Stock Agent.app created"

print
print "${GREEN}============================================================${RESET}"
print "${GREEN}INSTALLATION COMPLETE${RESET}"
print "${GREEN}============================================================${RESET}"
print
print "Created:"
print "  $APP_PATH"
print
print "Keep LSEG Workspace open and signed in for deep LSEG research and screening."
print "Opening Stock Agent now..."
/usr/bin/open "$APP_PATH"
print
print "Runtime log:"
print "  $LOG_FILE"
print
read -k 1 "?Press any key to close this installer window."
print
