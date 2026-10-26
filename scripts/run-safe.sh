#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
GUARD="$ROOT/scripts/guard.py"

if [[ ! -f "$GUARD" ]]; then
  printf 'guard not found: %s\n' "$GUARD" >&2
  exit 66
fi
if ! command -v systemd-run >/dev/null 2>&1; then
  printf 'systemd-run is required\n' >&2
  exit 69
fi

unit="dp-week1-${UID}-$(date +%s)-$$"
args=(
  systemd-run
  --user
  --wait
  --pipe
  --collect
  "--unit=$unit"
  "--working-directory=$ROOT"
  "--property=MemoryMax=8G"
  "--property=MemorySwapMax=512M"
  "--property=CPUQuota=400%"
  "--property=KillMode=control-group"
  "--property=TimeoutStopSec=15s"
  "--setenv=PYTHONUNBUFFERED=1"
)

for name in \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY \
  http_proxy https_proxy all_proxy no_proxy
do
  if [[ -n "${!name-}" ]]; then
    args+=("--setenv=$name=${!name}")
  fi
done

exec "${args[@]}" /usr/bin/env python3 "$GUARD" "$@"
