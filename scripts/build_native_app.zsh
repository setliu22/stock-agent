#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_ROOT="$PROJECT_ROOT/native"
APP_PATH="$PROJECT_ROOT/Stock Agent.app"
BUILD_TEMP="$(mktemp -d -t stock-agent-native-build)"
STAGED_APP="$BUILD_TEMP/Stock Agent.app"
PREVIOUS_APP="$BUILD_TEMP/previous.app"

cleanup() {
    /bin/rm -rf "$BUILD_TEMP"
}
trap cleanup EXIT

swift build -c release --package-path "$PACKAGE_ROOT"
BIN_DIR="$(swift build -c release --package-path "$PACKAGE_ROOT" --show-bin-path)"

mkdir -p "$STAGED_APP/Contents/MacOS" "$STAGED_APP/Contents/Resources"
/usr/bin/ditto "$PACKAGE_ROOT/Resources/Info.plist" "$STAGED_APP/Contents/Info.plist"
/usr/bin/ditto "$BIN_DIR/StockAgent" "$STAGED_APP/Contents/MacOS/StockAgent"
/usr/bin/ditto "$PROJECT_ROOT/assets/stock-agent.icns" "$STAGED_APP/Contents/Resources/StockAgent.icns"
chmod 755 "$STAGED_APP/Contents/MacOS/StockAgent"

# Use an ad-hoc identity for this local build.
/usr/bin/codesign --force --deep --sign - --timestamp=none "$STAGED_APP"
/usr/bin/codesign --verify --deep --strict "$STAGED_APP"

if [[ -d "$APP_PATH" ]]; then
    if [[ -x "$APP_PATH/Contents/MacOS/applet" && ! -e "$PROJECT_ROOT/backups/Stock Agent Python Legacy.app" ]]; then
        mkdir -p "$PROJECT_ROOT/backups"
        mv "$APP_PATH" "$PROJECT_ROOT/backups/Stock Agent Python Legacy.app"
    else
        mv "$APP_PATH" "$PREVIOUS_APP"
    fi
fi

if ! mv "$STAGED_APP" "$APP_PATH"; then
    if [[ -d "$PREVIOUS_APP" ]]; then mv "$PREVIOUS_APP" "$APP_PATH"; fi
    exit 1
fi

touch "$APP_PATH"
print "Built and ad-hoc signed: $APP_PATH"
