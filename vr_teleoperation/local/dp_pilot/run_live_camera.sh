#!/usr/bin/env bash
set -eu
here=$(cd -- "$(dirname -- "$0")" && pwd)
cam="$here/../four_camera"
python="$here/../../upstream/dora-openarm-data-collection/.venv/bin/python"
sock="$HOME/.cache/openarm-dp-field/camera.sock"
if [[ ! -t 0 || -e "$sock" ]]; then
  echo "需要现场终端，且不能覆盖已有相机服务" >&2
  exit 1
fi
read -r -p '仅打开四路相机，不访问 CAN。允许取流请输入 CAMERAS: ' confirm
[[ "$confirm" == CAMERAS ]] || exit 1
"$python" -B -c 'import sys;sys.path.insert(0,sys.argv[1]);from check_four_cameras import inventory;inventory()' "$here/../camera_bandwidth"
mkdir -p -m 700 "$(dirname -- "$sock")"
exec "$python" -B "$cam/service.py" --allow-cameras --socket "$sock" --max-seconds 300
