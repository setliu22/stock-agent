#!/bin/zsh

set -u

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
UPDATER="$PROJECT_ROOT/Update Stock Agent.command"

if [[ ! -x "$UPDATER" ]]; then
    print "Update Stock Agent.command is missing or is not executable."
    print
    read -k 1 "?Press any key to close."
    exit 1
fi

# The updater fetches the current branch, installs dependencies, rebuilds the
# app, and opens it after a successful installation.
exec "$UPDATER"
