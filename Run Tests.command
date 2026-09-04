#!/bin/zsh

set -u
set -o pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1

clear
echo "Stock Agent Test Runner"
echo
echo "Running native Swift tests…"
/usr/bin/swift test --package-path "$PROJECT_ROOT/native"
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
