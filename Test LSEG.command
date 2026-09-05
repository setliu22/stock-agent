#!/bin/zsh

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "The virtual environment is missing. Run Update Stock Agent.command first."
    echo
    read -k 1 "?Press any key to close."
    exit 1
fi

echo "Keep LSEG Workspace open and signed in."
echo "Checking the native app's read-only Workspace connection."
echo
print -r -- '{"operation":"status"}' | "$PYTHON" "$PROJECT_ROOT/scripts/lseg_bridge.py"
STATUS=$?
echo
read -k 1 "?Press any key to close."
echo
exit $STATUS
