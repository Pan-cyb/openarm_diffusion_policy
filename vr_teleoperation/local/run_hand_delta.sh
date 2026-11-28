#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
case "${1:-model}" in
  model)
    if (( $# > 1 )); then echo "Usage: bash $0 model" >&2; exit 2; fi
    exec "$here/../upstream/dora-openarm-data-collection/.venv/bin/python" \
      "$here/neck_sim.py" --listen 10.42.0.1 --ui
    ;;
  capture)
    if (( $# != 1 )); then echo "Usage: bash $0 capture" >&2; exit 2; fi
    exec bash "$here/run_webxr_real_pedestal.sh" --leader hand-delta
    ;;
  check)
    exec python3 "$here/four_camera/quest_wear.py" \
      /home/pan/.local/opt/android-platform-tools-20261107/platform-tools/adb --check-only
    ;;
  -h|--help) echo "check：只读检查 Quest USB；model（默认）：模型＋8091；capture：四相机真机采集＋8000，需要现场 START。" ;;
  *) echo "Usage: bash $0 [check|model|capture]" >&2; exit 2 ;;
esac
