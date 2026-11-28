"""One supervised episode-23 replay. No VR, camera, or policy inference."""
import argparse
import fcntl
import json
from pathlib import Path
import select
import socket
import subprocess
import sys
import time

import numpy as np
from openarm_driver import Config

from field_reference import DT, RATE, reference, replay_config_type, screen, screen_reference, SHA

HERE = Path(__file__).resolve().parent
SIDES = ("left","right")


def session(percent, folder, home, start, *, key, spawn):
    links, children, checked, feedback = {}, {}, set(), {}
    enabled, homed, scaled, finished, disabled = set(), set(), set(), set(), set()
    phase, result = "checking", False
    reported_faults = set()
    def send(kind, **kw):
        data = json.dumps(dict(kind=kind, **kw)).encode()
        for side,link in links.items():
            if kind=="ping" and side not in checked:
                continue  # No queued stale leases during imports/model loading.
            try:
                link.send(data)
            except OSError as exc:
                raise RuntimeError(f"{side}: 本地 {kind} 通信失败: {exc}") from exc
    with (folder/"trace.jsonl").open("x", buffering=1) as log:
        try:
            for side in SIDES:
                parent, child = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
                links[side] = parent
                with child:
                    children[side] = spawn(side,child,percent)
                parent.setblocking(False)
            phase_start = time.monotonic()
            def enter(name, text):
                nonlocal phase, phase_start
                phase, phase_start = name, time.monotonic()
                print(text,flush=True)
            while True:
                now = time.monotonic()
                if now-phase_start > {"checking":8,"inspect":90,"enabling":5,"home":12,
                        "home_ready":25,"approach":25,"waiting_go":15,"playing":12,"finish":5}[phase]:
                    raise TimeoutError("phase timeout: "+phase)
                for side,link in links.items():
                    for _ in range(64):
                        try: raw = link.recv(8192)
                        except BlockingIOError: break
                        if not raw: raise RuntimeError(side+" worker disconnected")
                        m = json.loads(raw)
                        kind = m["kind"]
                        if kind != "feedback":
                            print(json.dumps(dict(side=side, **m),allow_nan=False),file=log)
                        if kind in ("fault","unconfirmed","disabled"):
                            if kind=="disabled": disabled.add(side)
                            if kind=="fault": reported_faults.add(side)
                            raise RuntimeError(f"{side}: {m.get('reason',kind)}")
                        if kind in ("feedback","checked","dispatch"):
                            feedback[side] = m["stamp"],m["q"]
                        if kind=="checked": checked.add(side)
                        if kind=="enabled": enabled.add(side)
                        if kind=="home_ready": homed.add(side)
                        if kind=="dispatch" and m["phase"]=="ready" and m["scale"]==percent/100:
                            scaled.add(side)
                        if kind=="finished": finished.add(side)
                send("ping", mono=time.monotonic())
                if phase=="checking" and len(checked)==2:
                    enter("inspect","两臂八轴未使能。检查回位路径、终点承托及实体急停；输入 START 才使能回加宽启动位。")
                if phase=="enabling" and len(enabled)==2:
                    send("release_home")
                    enter("home","双臂使能已确认，回加宽启动位（每侧向外 4 cm）。")
                if phase=="home" and len(homed)==2:
                    enter("home_ready","加宽启动位已通过关节到位检查。确认到片段起点的路径净空，输入 APPROACH 才开始过渡。")
                if phase=="approach" and len(scaled)==2:
                    enter("waiting_go","双臂到起点、补偿已到所选比例。输入 GO 复现一次；STOP 或 Ctrl+C 停机。")
                if phase=="playing" and len(finished)==2:
                    enter("finish","轨迹结束，终点短暂停留；5 秒内自动失能，不回停车位。")
                    result = True
                if phase=="finish" and time.monotonic()-phase_start>=3: break
                cmd = key()
                if cmd=="STOP": break
                if cmd=="START" and phase=="inspect":
                    if any(not 0<=time.time_ns()-feedback[s][0]<150_000_000 for s in SIDES):
                        raise RuntimeError("stale preflight")
                    q = np.concatenate([feedback[s][1] for s in SIDES])
                    screen(np.linspace(q,home,101))
                    send("home",confirm="START")
                    enter("enabling","等待双臂使能确认。")
                elif cmd=="APPROACH" and phase=="home_ready":
                    if any(not 0<=time.time_ns()-feedback[s][0]<150_000_000 for s in SIDES):
                        raise RuntimeError("stale home feedback")
                    q = np.concatenate([feedback[s][1] for s in SIDES])
                    # Check a second approach from the actual held posture, not just nominal home.
                    bound = np.arange(1+int(np.ceil(np.max(abs(start-q)/RATE)/DT)))[:,None]*DT*RATE
                    screen(q+np.clip(start-q,-bound,bound))
                    send("task_start")
                    enter("approach","限速接近起点并在原位爬升补偿。")
                elif cmd=="GO" and phase=="waiting_go":
                    send("replay",at=time.monotonic()+.12)
                    enter("playing","执行固定 10 秒轨迹。")
                time.sleep(.009)
        except (KeyboardInterrupt, Exception) as exc:
            result = False
            print("停止：",repr(exc),flush=True)
            print(json.dumps({"stop_reason":repr(exc)}),file=log)
        finally:
            for link in links.values():
                try:
                    link.send(b'{"kind":"stop"}')
                    link.shutdown(socket.SHUT_WR)
                except OSError: pass
            until = time.monotonic()+5
            while links and time.monotonic()<until:
                ready,_,_ = select.select(list(links.values()),[],[],.1)
                for side, link in list(links.items()):
                    if link not in ready: continue
                    try: raw = link.recv(8192)
                    except OSError: raw = b""
                    if not raw:
                        link.close()
                        del links[side]
                        continue
                    m=json.loads(raw)
                    if m["kind"]=="disabled": disabled.add(side)
                    if m["kind"]=="fault" and side not in reported_faults:
                        print(f"{side} 子进程停机原因：{m.get('reason')}",flush=True)
                        reported_faults.add(side)
                    print(json.dumps(dict(side=side,**m)),file=log)
            for link in links.values(): link.close()
            for child in children.values():
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    print("进程尚未退出；现场处置，不得启动新会话。",flush=True)
                    result=False
    print("未使能新回包确认：",sorted(disabled),"（不是物理停稳证明）。日志：",folder)
    return result and disabled==set(SIDES)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("percent",choices=(0,25,50,75,100),type=int)
    p.add_argument("--inspect-only",action="store_true",help="offline model and data only")
    args=p.parse_args()
    path,home=reference(replay_config_type(Config)("openarm_pedestal"))
    screen_reference(path,home)
    print("启动位每臂向外 4 cm；片段 23 / 帧 32–52，通过模型及限速检查；不代表现场安全。",flush=True)
    if args.inspect_only: return 0
    if not sys.stdin.isatty(): p.error("现场终端必需")
    lockdir=Path.home()/".cache/openarm-dp-field"
    lockdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (lockdir/"verify.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        folder=lockdir/"fixed-replay"/str(time.time_ns())
        folder.mkdir(parents=True,mode=0o700)
        (folder/"manifest.json").write_text(json.dumps(dict(episode=23,frames=[32,52],
            sha256=SHA,percent=args.percent,dt=.02,source="action",units="rad",
            home_target_rad=home.tolist(),home_outward_m_per_side=.04)))
        def key():
            if not select.select([sys.stdin],[],[],0)[0]: return None
            line=sys.stdin.readline()
            return line.strip() if line else "STOP"
        def spawn(side,sock,percent):
            return subprocess.Popen([sys.executable,"-B",str(HERE/"motion_arm.py"),"--side",side,
                "--replay-percent",str(percent),"--fd",str(sock.fileno())],
                pass_fds=(sock.fileno(),),stdin=subprocess.DEVNULL,start_new_session=True)
        print("只读检查中。STOP 回车或 Ctrl+C 中止；失能可能下落。",flush=True)
        return 0 if session(args.percent,folder,home,path[0],key=key,spawn=spawn) else 1


if __name__=="__main__": sys.exit(main())
