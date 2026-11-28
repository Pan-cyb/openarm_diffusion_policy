"""Renew simulated wear once, before starting the confirmed capture dataflow."""

import subprocess
import sys
import time


class QuestUsbPermissionError(RuntimeError):
    pass


def _run(adb, *args):
    try:
        return subprocess.check_output([adb, *args], stderr=subprocess.PIPE,
                                       text=True, timeout=10).strip()
    except subprocess.CalledProcessError as error:
        reason = (error.stderr or "").strip() or str(error)
        if any(text in reason.lower() for text in ("insufficient permissions", "no permissions")):
            raise QuestUsbPermissionError(
                reason + "\n可在普通用户终端运行 bash vr_teleoperation/local/setup_quest_usb.sh"
            ) from error
        raise RuntimeError(reason) from error


def check_quest(adb):
    serial = _run(adb, "-d", "get-serialno")
    if not serial or serial == "unknown":
        raise ValueError("没有已授权的 USB Quest；请检查 USB 调试连接")

    if _run(adb, "-s", serial, "shell", "getprop", "ro.product.model") != "Quest 3":
        raise ValueError("USB 设备不是 Quest 3，未修改设置")
    return serial


def enable_for_capture(adb):
    serial = check_quest(adb)

    def shell(*args):
        return _run(adb, "-s", serial, "shell", *args)
    shell("am", "broadcast", "-a", "com.oculus.vrpowermanager.prox_close",
          "--ei", "duration", "28800000")
    for attempt in range(5):
        state = shell("dumpsys", "oculus.internal.power.IVrPowerManager/default").splitlines()
        if "Virtual proximity state: CLOSE" in state and "State: HEADSET_MOUNTED" in state:
            print("Quest 3 已请求 8 小时模拟佩戴，已确认当前为佩戴状态。", flush=True)
            return
        if attempt < 4:
            time.sleep(.2)
    raise RuntimeError("未能从头显确认模拟佩戴状态")


if __name__ == "__main__":
    try:
        if sys.argv[2:] == ["--check-only"]:
            check_quest(sys.argv[1])
            print("Quest 3 ADB 就绪（只读）；未开启相机、CAN 或机械臂。")
        elif len(sys.argv) == 2:
            enable_for_capture(sys.argv[1])
        else:
            sys.exit("usage: quest_wear.py ADB [--check-only]")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(f"Quest 模拟佩戴准备失败，未启动机器人数据流：{error}")
