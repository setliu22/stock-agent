#!/bin/zsh

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "The virtual environment is missing. Run Install Stock Agent.command first."
    echo
    read -k 1 "?Press any key to close."
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
echo "Starting Stock Agent with:"
"$PYTHON" -c "import sys; print(sys.executable)"
echo
echo "Startup errors will remain visible here."
echo
exec "$PYTHON" "$PROJECT_ROOT/stock_agent_launcher.py"
