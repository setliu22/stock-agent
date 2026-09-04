#!/bin/zsh

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$PROJECT_ROOT/Stock Agent.app"

if [[ ! -x "$APP_PATH/Contents/MacOS/StockAgent" ]]; then
    print "Building the native macOS app for the first time…"
    /bin/zsh "$PROJECT_ROOT/scripts/build_native_app.zsh"
fi

exec /usr/bin/open "$APP_PATH"
