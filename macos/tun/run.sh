#!/bin/sh
# Fixed root-owned executable, rules, configuration and working directory.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
base=/Library/PrivilegedHelperTools/io.day8gateway.tun
anchor=com.apple/000.day8gateway
cd "$base"
# Leave system policy intact. Refuse startup when the expected hook is missing.
pfctl -sr 2>/dev/null | grep -Fq 'anchor "com.apple/*"'
pfctl -a "$anchor" -f "$base/public-udp.pf"
token=/var/run/io.day8gateway.pf-token
if [ ! -s "$token" ] || ! pfctl -si 2>/dev/null | grep -q 'Status: Enabled'; then
    result=$(pfctl -E 2>&1)
    value=$(echo "$result" | awk '/Token/ {print $NF}')
    case "$value" in ''|*[!0-9]*) exit 1;; esac
    echo "$value" > "$token"
fi
exec "$base/mihomo" -d "$base" -f "$base/config.json"
