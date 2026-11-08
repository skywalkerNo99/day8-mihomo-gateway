#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out="${1:-$HOME/Applications}"
mkdir -p "$out"
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
app="$build/Day8 Gateway.app"
mkdir -p "$app/Contents/MacOS" "$build/cache"
xcrun swiftc -parse-as-library -O -module-cache-path "$build/cache" "$repo/macos/Day8Console.swift" "$repo/macos/OfficialRoutes.swift" "$repo/macos/DomainTools.swift" -o "$app/Contents/MacOS/Day8Console"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>Day8Console</string>
<key>CFBundleIdentifier</key><string>io.day8gateway.console</string>
<key>CFBundleName</key><string>Day8 Gateway</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app"
codesign --verify --strict "$app"
/usr/bin/ditto "$app" "$out/Day8 Gateway.app"
codesign --verify --strict "$out/Day8 Gateway.app"
