#!/usr/bin/env bash
# One operator entry point; never submit START or launch an arm worker here.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
pilot="$root/vr_teleoperation/local/dp_pilot"
python="$root/.venv-dp/bin/python"
unset OPENARM_DP_RELEASE
deployment=
case "${1:-}" in
  --help|-h) printf '用法：%s [--new-cnn|--new-transformer [--inspect-only|--align-scene|--compare-start [会话目录]] | --no-gravity | --fixed-replay 0|25|50|75|100 [--inspect-only] | --pdff-sweep | --pdff-sim [--pose home|bent] | --setup-permissions | --check | --camera-timing | --replay-last | --align-scene | --compare-start [会话目录]]\n默认 DP 使用原 PD＋100%% 模型重力；--new-cnn/--new-transformer 选择已绑定 release 的新模型且仍使用 100%% 模型重力。--inspect-only 仅离线校验并加载所选新模型。\n' "$0"; exit 0 ;;
  --new-cnn|--new-transformer)
    deployment=${1#--new-}
    export OPENARM_DP_RELEASE=$deployment
    shift
    [[ -x "$python" ]] || { echo '缺少训练环境。' >&2; exit 1; }
    export PYTHONPATH="$root/openarm_v2/upstream/diffusion_policy${PYTHONPATH:+:$PYTHONPATH}"
    inspect_args=()
    [[ "${1:-}" == --inspect-only ]] && inspect_args+=(--load)
    "$python" -B "$pilot/deployment_release.py" "$deployment" "${inspect_args[@]}"
    case "${1:-}" in
      --inspect-only) (( $# == 1 )) || exit 2; exit 0 ;;
      --align-scene) (( $# == 1 )) || exit 2
        exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/align_scene.py" live ;;
      --compare-start) (( $# <= 2 )) || exit 2; shift
        exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/align_scene.py" saved "$@" ;;
      '') ;;
      *) printf '新模型未知参数：%s\n' "$1" >&2; exit 2 ;;
    esac ;;
  --fixed-replay)
    [[ $# -ge 2 && "${2:-}" =~ ^(0|25|50|75|100)$ ]] || exit 2
    if [[ $# == 3 && $3 == --inspect-only ]]; then
      exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/field_replay.py" "$2" --inspect-only
    fi
    [[ $# == 2 && -t 0 ]] || { echo '需要本地终端；可先使用 --inspect-only。'; exit 2; } ;;
  --pdff-sweep) shift
    exec "$python" -B "$pilot/pdff_sweep.py" "$@" ;;
  --pdff-sim) shift
    exec "$python" -B "$pilot/pedestal_pdff.py" "$@" ;;
  --align-scene) (( $# == 1 )) || exit 2
    exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/align_scene.py" live ;;
  --compare-start) (( $# <= 2 )) || exit 2
    shift
    exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/align_scene.py" saved "$@" ;;
  --replay-last) (( $# == 1 )) || exit 2
    exec nice -n 10 "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/replay_trial.py" ;;
  --setup-permissions) (( $# == 1 )) || exit 2
    exec bash "$pilot/setup_workbench_permissions.sh" ;;
  --camera-timing) (( $# == 1 )) || exit 2
    exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/check_camera_timing.py" ;;
  ''|--check|--trial-left-j5|--no-gravity) ;;
  *) printf '未知参数：%s\n' "$1" >&2; exit 2 ;;
esac
if [[ -z "$deployment" && "${1:-}" != --fixed-replay ]]; then
(( $# <= 1 )) || exit 2
[[ -x "$python" && -x "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" &&
   -r "$root/vr_teleoperation/outputs/dp_baseline49_20261123/run/pilot.pt" &&
   -r "$root/vr_teleoperation/outputs/dp_baseline49_20261123/data/ep12.npz" ]] ||
  { echo '缺少训练环境、驱动环境、权重或共同起点示范文件。' >&2; exit 1; }
fi
if [[ -n "$deployment" ]]; then
  [[ $# == 0 && -x "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" ]] ||
    { echo '缺少驱动环境或新模型参数未清空。' >&2; exit 1; }
fi

# Use netlink metadata only. Check BOTH adapters before changing either one.
inspect() {
  python3 - <<'PY'
import json, subprocess
rows = {r["ifname"]: r for r in json.loads(subprocess.check_output(
    ["ip", "-j", "-details", "link", "show", "type", "can"]))}
down = []
for name in ("can0", "can1"):
    row = rows.get(name, {})
    if (row.get("parentbus"), row.get("parentdev")) != ("usb", "3-2.1:1.0"):
        raise SystemExit(f"{name}: 未检测到项目指定 USB 端口的双路适配器")
    if "UP" not in row["flags"]:
        down.append(name)
        continue
    cfg = row["linkinfo"]["info_data"]
    bit, data = cfg.get("bittiming", {}), cfg.get("data_bittiming", {})
    if not (cfg.get("ctrlmode") == ["FD"] and cfg.get("state") == "ERROR-ACTIVE"
            and cfg.get("restart_ms") == 0 and bit.get("bitrate") == 1000000
            and data.get("bitrate") == 5000000 and bit.get("sample_point") == .75
            and data.get("sample_point") == .75 and data.get("sjw") == 2):
        raise SystemExit(f"{name}: 已启用但状态或参数不符，不重配活动接口")
print(" ".join(down))
PY
}
down=$(inspect)
if [[ "${1:-}" == --fixed-replay ]]; then
  [[ -z "$down" ]] || { echo "CAN 尚未就绪：$down；不会自动重配，请先按现场既有流程准备，再停止其他控制程序。"; exit 1; }
  mkdir -p -m 700 "$HOME/.cache/openarm-dp-field"
  exec 9>"$HOME/.cache/openarm-dp-field/motion-launch.lock"
  flock -n 9 || { echo '已有运动面板或回放会话。' >&2; exit 1; }
  exec "$root/vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python" -B "$pilot/field_replay.py" "$2"
fi
if [[ "${1:-}" == --check ]]; then
  [[ -z "$down" ]] || { echo "接口未启用：$down（未修改）"; exit 1; }
  echo '启动文件与 CAN 配置检查通过；未打开相机或机械臂。'
  exit 0
fi
[[ -n "${DISPLAY:-}" ]] || { echo '请在本机图形桌面的终端运行。' >&2; exit 1; }
mkdir -p -m 700 "$HOME/.cache/openarm-dp-field"
exec 9>"$HOME/.cache/openarm-dp-field/motion-launch.lock"
flock -n 9 || { echo '运动面板已由此脚本打开，请使用已有窗口。' >&2; exit 1; }
if [[ -n "$down" ]]; then
  echo "准备主机 CAN 接口：$down（非交互授权；无权限请先运行 --setup-permissions）"
  for dev in $down; do
    sudo -n /usr/sbin/ip link set dev "$dev" type can bitrate 1000000 sample-point 0.75 restart-ms 0 \
      dbitrate 5000000 fd on dsample-point 0.75 dsjw 2
    sudo -n /usr/sbin/ip link set dev "$dev" up
  done
  down=$(inspect)
  [[ -z "$down" ]] || { echo "CAN 未就绪：$down" >&2; exit 1; }
fi
echo '① 查状态 → ② 确认使能并到官方启动位 → ③ 示范起点 → ④ 开始 DP。复位不运动。'
export PYTHONPATH="$root/openarm_v2/upstream/diffusion_policy${PYTHONPATH:+:$PYTHONPATH}"
exec "$python" -B "$pilot/live_motion.py" "$@"
