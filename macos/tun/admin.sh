#!/bin/sh
# One-shot administrator operations. launchd never executes anything from the checkout.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
base=/Library/PrivilegedHelperTools/io.day8gateway.tun
label=io.day8gateway.tun
job=/Library/LaunchDaemons/$label.plist
anchor=com.apple/000.day8gateway
token=/var/run/io.day8gateway.pf-token
[ "$(id -u)" = 0 ] || exit 1
case "${1:-}" in
prepare)
    src=$(cd "$(dirname "$0")" && pwd)
    [ ! -e "$base" ] && [ ! -L "$base" ] && [ ! -e "$job" ] || { echo "TUN installation already exists."; exit 1; }
    case "$(uname -m)" in
        arm64) digest=3615d13fbddd8b8e752f95f492c8887ba629671457fbe55ba80d523d6e2e03c3 ;;
        x86_64) digest=5e71b3dff12af523858da02cc2b7e872a84c657554431766b4b3c61774e9f06d ;;
        *) exit 2 ;;
    esac
    read -r key
    [ "${#key}" = 64 ] || exit 2
    case "$key" in *[!0-9a-f]*) exit 2;; esac
    mkdir -m 700 "$base"
    trap 'rm -f "$job"; rm -rf "$base"' EXIT
    # Take a private copy before checking and unpacking it.
    cp "$2" "$base/core.gz"
    if [ "$(shasum -a 256 "$base/core.gz" | cut -d' ' -f1)" = "$digest" ]; then
        gzip -dc "$base/core.gz" > "$base/mihomo"
    else
        echo "Mihomo checksum mismatch."; exit 2
    fi
    rm -f "$base/core.gz"
    chmod 500 "$base/mihomo"
    # Only a fixed template and a 64-character local handoff key enter the root config.
    while IFS= read -r line; do
        case "$line" in
            *'__HANDOFF_KEY__'*) printf '%s%s%s\n' "${line%%__HANDOFF_KEY__*}" "$key" "${line#*__HANDOFF_KEY__}";;
            *) printf '%s\n' "$line";;
        esac
    done < "$src/capture.json.in" > "$base/config.json"
    install -m 500 "$src/run.sh" "$base/run.sh"
    install -m 400 "$src/public-udp.pf" "$base/public-udp.pf"
    "$base/mihomo" -t -d "$base" -f "$base/config.json" >/dev/null
    pfctl -n -a "$anchor" -f "$base/public-udp.pf"
    install -o root -g wheel -m 644 "$src/$label.plist" "$job"
    trap - EXIT
    ;;
guard)
    pfctl -sr 2>/dev/null | grep -Fq 'anchor "com.apple/*"'
    [ -z "$(pfctl -a "$anchor" -sr 2>/dev/null)" ]
    # Only flush an anchor that this installation successfully reserved.
    touch "$base/owns-anchor"
    # Establish the packet filter before the previous tunnel is released.
    pfctl -a "$anchor" -f "$base/public-udp.pf"
    result=$(pfctl -E 2>&1)
    value=$(echo "$result" | awk '/Token/ {print $NF}')
    case "$value" in ''|*[!0-9]*) exit 1;; esac
    echo "$value" > "$token"
    ;;
start) launchctl bootstrap system "$job" ;;
restart)
    # A normal shutdown commits fake-IP mappings before launchd's KeepAlive restart.
    pid_of_core() { launchctl print "system/$label" | awk '$1=="pid" && $2=="=" {print $3;exit}'; }
    previous=$(pid_of_core)
    [ -n "$previous" ] || exit 1
    launchctl kill SIGTERM "system/$label"
    tries=0
    while [ "$tries" -lt 100 ]; do
        current=$(pid_of_core)
        if [ -n "$current" ] && [ "$current" != "$previous" ]; then exit 0; fi
        tries=$((tries+1))
        sleep .25
    done
    exit 1
    ;;
stop) if launchctl print "system/$label" >/dev/null 2>&1; then launchctl bootout "system/$label"; fi ;;
remove)
    if launchctl print "system/$label" >/dev/null 2>&1; then launchctl bootout "system/$label"; fi
    if [ -f "$base/owns-anchor" ] || [ -s "$token" ]; then pfctl -a "$anchor" -F rules; fi
    if [ -s "$token" ]; then pfctl -X "$(cat "$token")"; rm -f "$token"; fi
    rm -f "$job"
    rm -rf "$base"
    ;;
udp-count)
    pfctl -a "$anchor" -vvsr 2>/dev/null | awk '/day8-public-udp/{found=1} found && /Packets:/ {for(i=1;i<=NF;i++) if($i=="Packets:"){print $(i+1);exit}}'
    ;;
*) exit 2 ;;
esac
