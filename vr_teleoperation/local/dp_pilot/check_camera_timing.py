"""Camera-only wall-clock alignment check; never imports the arm or policy."""
from contextlib import closing
import fcntl
from pathlib import Path
import subprocess
import sys
import tempfile
import time

LOCAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOCAL/"four_camera"))
from bridge import Client, NAMES
from shadow_inputs import PERIOD_NS


def check(stream, count=300):
    worst = dict.fromkeys(NAMES, 0.)
    anchor = time.time_ns()+PERIOD_NS
    for _ in range(count):
        time.sleep(max(0, (anchor-time.time_ns())/1e9))
        status, frames = stream.request(f"dp_snapshot {anchor}")
        if (status.get("mock") is not False or status.get("dp_host_anchor_ns") != anchor
                or not status.get("ready") or not status.get("basic_image_ready")
                or status.get("snapshot_error")):
            raise ValueError(status.get("snapshot_error") or status.get("error") or "invalid camera status")
        if sorted(f.metadata["camera"] for f in frames) != sorted(NAMES):
            raise ValueError("missing or duplicate camera")
        for f in frames:
            age = anchor-f.metadata["timestamp_ns"]
            name = f.metadata["camera"]
            if not 0 <= age < PERIOD_NS:
                raise ValueError(f"{name} age={age/1e6:.1f} ms")
            worst[name] = max(worst[name], age/1e6)
        if not 0 <= time.time_ns()-anchor < PERIOD_NS:
            raise TimeoutError("四路快照传输错过 100 ms 时限")
        anchor += PERIOD_NS
    return worst


def main():
    cache = Path.home()/".cache/openarm-dp-field"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cache/"motion-launch.lock").open("a") as panel, (cache/"verify.lock").open("a") as owner:
        for lock in (panel, owner):
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (cache/"camera.sock").exists():
            raise RuntimeError("请先结束旧相机服务，不并行打开相机")
        sys.path.insert(0,str(LOCAL/"camera_bandwidth"))
        from check_four_cameras import inventory
        inventory()
        with tempfile.TemporaryDirectory(prefix="dp-timing-") as tmp:
            path = Path(tmp)/"camera.sock"
            proc = subprocess.Popen([sys.executable,"-B",str(LOCAL/"four_camera/service.py"),
                                     "--allow-cameras","--socket",str(path),"--max-seconds","90","--privilege","workbench"])
            try:
                until = time.monotonic()+40
                while time.monotonic()<until and proc.poll() is None:
                    if path.is_socket():
                        with closing(Client(path)) as c:
                            if c.health().get("basic_image_ready"): break
                    time.sleep(.1)
                else: raise RuntimeError("真实相机未就绪")
                print("仅测相机：10 Hz，300 组快照，不访问机械臂。",flush=True)
                with closing(Client(path)) as c:
                    worst = check(c)
            finally:
                if proc.poll() is None: proc.terminate()
                proc.wait()
            if proc.returncode:
                raise RuntimeError("相机服务未正常退出，请检查恢复日志")
            print("300 组通过，各路最大帧龄(ms)：", {k:round(v,1) for k,v in worst.items()})


if __name__ == "__main__":
    try: main()
    except BlockingIOError: sys.exit("已有面板占用，请先关闭旧面板。")
    except (OSError, ValueError, RuntimeError) as exc: sys.exit(f"相机时序检查未通过：{exc}")
