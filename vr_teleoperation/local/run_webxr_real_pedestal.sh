#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
vr_root=$(cd -- "$script_dir/.." && pwd)
upstream="$vr_root/upstream/dora-openarm-data-collection"

# A capture launcher supplies its prepared graph when it calls back here.
if [[ -z "${OPENARM_REAL_DATAFLOW:-}" ]]; then
  exec "$upstream/.venv/bin/python" "$script_dir/four_camera/launch.py" \
    --mode real --allow-cameras --allow-robot \
    --operator "${OPENARM_OPERATOR:-${USER:-unspecified}}" \
    --location "${OPENARM_LOCATION:-unspecified}" --max-seconds 3600 "$@"
fi

dataflow="$OPENARM_REAL_DATAFLOW"
adb="/home/pan/.local/opt/android-platform-tools-20261107/platform-tools/adb"
prepare="$script_dir/prepare_webxr_real_pedestal.sh"
adb_reverse="$script_dir/webxr_adb_reverse_watch.sh"
capture_cli="${OPENARM_CAPTURE_CLI:-}"

if [[ -n "$capture_cli" ]]; then
  if [[ "$capture_cli" != "$script_dir/four_camera/dora_capture_cli.py" ]]; then
    printf 'Unexpected capture CLI override: %s\n' "$capture_cli" >&2
    exit 1
  fi
  "$upstream/.venv/bin/python" "$capture_cli" --version
fi

for required in \
  "$script_dir/webxr_http_localhost.py" \
  "$script_dir/leader_webxr_real_pedestal.yaml" \
  "$script_dir/robot_real_pedestal.yaml" \
  "$prepare" \
  "$adb_reverse" \
  "$dataflow" \
  "$adb" \
  "$upstream/.venv/bin/dora"
do
  if [[ ! -e "$required" ]]; then
    printf 'Missing required file: %s\n' "$required" >&2
    exit 1
  fi
done

mkdir -p -m 700 "$HOME/.cache/openarm-dp-field"
exec 8>"$HOME/.cache/openarm-dp-field/verify.lock"
flock -n 8 || { echo '回放、DP 或其他实机所有者已占用；不重配 CAN。' >&2; exit 1; }
if [[ "${OPENARM_LEADER:-webxr}" == "hand-delta" ]]; then
  if ss -Hlun 'sport = :5006' | grep -q .; then
    echo 'UDP 5006 已被占用；请先退出 neck_sim.py 或旧接收器。未启动机器人数据流。' >&2
    exit 1
  fi
fi
printf 'Desktop UI: http://127.0.0.1:8000/\n'
printf 'Robot: real OpenArm V2 pedestal on can0/can1\n'
printf 'WARNING: the official UI starts and enables both arms on boot.\n'
if [[ "${OPENARM_CAPTURE_START_CONFIRMED:-}" == "1" \
      && "$capture_cli" == "$script_dir/four_camera/dora_capture_cli.py" ]]; then
  printf 'START already confirmed in the four-camera launcher; starting the prepared dataflow.\n'
else
  read -r -p 'Type START to enable both arms and run the dataflow: ' confirmation
  if [[ "$confirmation" != "START" ]]; then
    printf 'Cancelled; the robot dataflow was not started.\n'
    exit 1
  fi
fi

if [[ -n "$capture_cli" ]]; then
  "$upstream/.venv/bin/python" "$script_dir/four_camera/quest_wear.py" "$adb"
fi
pkexec "$prepare"
if [[ "${OPENARM_LEADER:-webxr}" == "hand-delta" ]]; then
  printf 'Quest: OpenArm Pose Transmitter -> 10.42.0.1:5006 (hand-delta), no WebXR.\n'
else
  "$adb_reverse" --once
  printf 'Quest WebXR URL: http://localhost:8080/\n'
fi
cd "$upstream"
if [[ -n "$capture_cli" ]]; then
  PATH="$upstream/.venv/bin:$PATH" \
    CMAKE_PREFIX_PATH=/home/pan/.local \
    PYTHONPATH="$vr_root/deps/dora-capture-sdk/python${PYTHONPATH:+:$PYTHONPATH}" \
    exec "$upstream/.venv/bin/python" "$capture_cli" run "$dataflow" --uv
fi
PATH="$upstream/.venv/bin:$PATH" \
  CMAKE_PREFIX_PATH=/home/pan/.local \
  exec "$upstream/.venv/bin/dora" run "$dataflow" --uv
