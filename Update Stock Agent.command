#!/bin/zsh

set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT" || exit 1
INSTALLER="$PROJECT_ROOT/Install Stock Agent.command"

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
print "${BLUE}Stock Agent Updater${RESET}"
print
print "Project folder:"
print "  $PROJECT_ROOT"
print

if ! command -v git >/dev/null 2>&1; then
    print "${RED}Git is not installed or is not available in PATH.${RESET}"
    pause_and_exit 1
fi
if [[ ! -d "$PROJECT_ROOT/.git" ]]; then
    print "${RED}This folder is not a Git checkout.${RESET}"
    print "Download or clone the repository before using the updater."
    pause_and_exit 1
fi
if [[ ! -x "$INSTALLER" ]]; then
    print "${RED}Install Stock Agent.command is missing or is not executable.${RESET}"
    pause_and_exit 1
fi

BRANCH="$(git branch --show-current)"
if [[ -z "$BRANCH" ]]; then
    print "${RED}The repository is not currently on a named branch.${RESET}"
    pause_and_exit 1
fi

# The installer regenerates Stock Agent.app locally. Ignore that generated app
# when checking whether source edits would be overwritten by an update.
SOURCE_CHANGES="$(git status --porcelain --untracked-files=no -- . ':(exclude)Stock Agent.app')"
if [[ -n "$SOURCE_CHANGES" ]]; then
    print "${YELLOW}The updater found local source changes and will not overwrite them:${RESET}"
    print
    print -r -- "$SOURCE_CHANGES"
    print
    print "Commit, stash, or copy those changes somewhere safe, then run this updater again."
    pause_and_exit 1
fi

print "Updating branch: $BRANCH"
print
git fetch origin "$BRANCH"
FETCH_STATUS=$?
if [[ $FETCH_STATUS -ne 0 ]]; then
    print
    print "${RED}Could not download the latest version from GitHub.${RESET}"
    print "Check your internet connection and GitHub access, then retry."
    pause_and_exit "$FETCH_STATUS"
fi

git merge --ff-only "origin/$BRANCH"
MERGE_STATUS=$?
if [[ $MERGE_STATUS -ne 0 ]]; then
    print
    print "${RED}The update could not be applied as a safe fast-forward.${RESET}"
    print "No local source files were overwritten."
    pause_and_exit "$MERGE_STATUS"
fi

print
print "${GREEN}Source update complete.${RESET}"
print "The installer will now refresh Python packages, run tests, and rebuild the app."
print
exec "$INSTALLER"
