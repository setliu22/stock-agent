#!/bin/zsh

set -u
set -o pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
PYTHON="$PROJECT_ROOT/.venv/bin/python"

clear
echo "Stock Agent Test Runner"
echo
if [[ ! -x "$PYTHON" ]]; then
    echo "The virtual environment is missing. Run Install Stock Agent.command first."
    echo
    read -k 1 "?Press any key to close."
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
echo "Using: $PYTHON"
"$PYTHON" --version
echo
echo "Running only this project's tests..."
"$PYTHON" -m pytest -c "$PROJECT_ROOT/pytest.ini" "$PROJECT_ROOT/tests"
STATUS=$?
echo
if [[ $STATUS -eq 0 ]]; then
    echo "All tests passed."
else
    echo "Tests failed with exit code $STATUS."
fi
echo
read -k 1 "?Press any key to close."
echo
exit $STATUS
