#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
vr_root=$(cd -- "$script_dir/.." && pwd)
upstream="$vr_root/upstream/dora-openarm-data-collection"
dataflow="$script_dir/dataflow_udp_pedestal.yaml"

for required in "$dataflow" "$upstream/.venv/bin/dora"
do
  if [[ ! -e "$required" ]]; then
    printf 'Missing required file: %s\n' "$required" >&2
    exit 1
  fi
done

if ss -lun | grep -qE '(^|:)5006([[:space:]]|$)'; then
  printf 'UDP port 5006 is already in use. Stop the other receiver first.\n' >&2
  exit 1
fi

printf 'Quest native UDP target: 10.42.0.1:5006\n'
printf 'Open OpenArm Pose Transmitter and press Connect.\n'
printf 'Model: %s\n' \
  "/home/pan/openarm_diffusion_policy/openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"
printf 'Press Ctrl+C in this terminal to stop the simulation.\n'

cd "$upstream"
PATH="$upstream/.venv/bin:$PATH" \
  CMAKE_PREFIX_PATH=/home/pan/.local \
  exec "$upstream/.venv/bin/dora" run "$dataflow" --uv
