#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
vr_root=$(cd -- "$script_dir/.." && pwd)
upstream="$vr_root/upstream/dora-openarm-data-collection"
dataflow="$script_dir/dataflow_webxr_pedestal.yaml"
adb="/home/pan/.local/opt/android-platform-tools-20261107/platform-tools/adb"

for required in \
  "$script_dir/webxr_http_localhost.py" \
  "$dataflow" \
  "$adb" \
  "$upstream/.venv/bin/dora"
do
  if [[ ! -e "$required" ]]; then
    printf 'Missing required file: %s\n' "$required" >&2
    exit 1
  fi
done

pkexec "$adb" reverse tcp:8080 tcp:8080
pkexec "$adb" reverse --list

printf 'Quest WebXR URL: http://localhost:8080/\n'
printf 'Model: %s\n' \
  "/home/pan/openarm_diffusion_policy/openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"
printf 'Press Ctrl+C in this terminal to stop the simulation.\n'

cd "$upstream"
PATH="$upstream/.venv/bin:$PATH" \
  CMAKE_PREFIX_PATH=/home/pan/.local \
  exec "$upstream/.venv/bin/dora" run "$dataflow" --uv
