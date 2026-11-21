#!/usr/bin/env bash
set -Eeuo pipefail

adb="/home/pan/.local/opt/android-platform-tools-20261107/platform-tools/adb"
local_port="${WEBXR_ADB_LOCAL_PORT:-8080}"
remote_port="${WEBXR_ADB_REMOTE_PORT:-8080}"
poll_interval="${WEBXR_ADB_POLL_INTERVAL:-3}"

if [[ ! -x "$adb" ]]; then
  printf 'Missing adb executable: %s\n' "$adb" >&2
  exit 1
fi

ensure_reverse() {
  local mapping
  mapping="tcp:${local_port} tcp:${remote_port}"

  if ! "$adb" get-state 2>/dev/null | grep -Fxq device; then
    return 1
  fi

  if "$adb" reverse --list 2>/dev/null |
    awk -v local_port="tcp:${local_port}" -v remote_port="tcp:${remote_port}" \
      '$2 == local_port && $3 == remote_port {found = 1} END {exit !found}'; then
    return 0
  fi

  "$adb" reverse "tcp:${local_port}" "tcp:${remote_port}" >/dev/null
  logger -t openarm-webxr-adb \
    "restored Quest tcp:${local_port} -> Ubuntu tcp:${remote_port}"
}

"$adb" start-server >/dev/null

if [[ "${1:-}" == "--once" ]]; then
  if ! timeout 15s "$adb" wait-for-device; then
    printf 'Quest was not available to ADB within 15 seconds.\n' >&2
    exit 1
  fi
  ensure_reverse
  printf 'ADB reverse: Quest tcp:%s -> Ubuntu tcp:%s\n' \
    "$local_port" \
    "$remote_port"
  exit 0
fi

while true; do
  ensure_reverse || true
  sleep "$poll_interval"
done
