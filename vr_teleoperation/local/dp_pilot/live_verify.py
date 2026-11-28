"""PC-only field verification: live observations, shadow inference, per-arm stop."""
import json
import fcntl
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from queue import Empty, SimpleQueue

HERE = Path(__file__).resolve().parent
SDK_PY = HERE.parents[1]/"upstream/dora-openarm-data-collection/.venv/bin/python"
from deployment_release import selected_release
RELEASE = selected_release()
BASE = RELEASE.checkpoint.parent.parent if RELEASE.legacy else RELEASE.checkpoint.parent
SIDES = ("left", "right")


class LiveSession:
    def __init__(self):
        self.locked = threading.Event()
        self.samples = {s: deque(maxlen=32) for s in SIDES}
        self.guard = threading.Lock()
        self.workers = {}
        self.events = SimpleQueue()
        self.opened_at = None
        self.stop_reason = None
        self.predictions_shown = 0

    def connect(self):
        if self.locked.is_set() or self.workers:
            raise RuntimeError("session already started or locked")
        directory = Path.home()/".cache/openarm-dp-field"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ownership = (directory/"verify.lock").open("a")
        try:
            fcntl.flock(self.ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.ownership.close()
            raise RuntimeError("已有真机验证面板占用，禁止重复连接") from exc
        self.opened_at = time.monotonic()
        try:
            for side in SIDES:
                p = subprocess.Popen([str(SDK_PY), "-B", str(HERE/"live_arm_probe.py"),
                                      "--field-session", side], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)
                self.workers[side] = p
                threading.Thread(target=self.read, args=(side,p), daemon=True).start()
        except Exception as exc:
            self.stop("启动失败："+str(exc))
            raise

    def stop(self, reason="操作员请求停止"):
        if self.stop_reason is None:
            self.stop_reason = reason
        self.locked.set()
        for p in tuple(self.workers.values()):
            # EOF is the stop request, not a signal killing the CAN owner.
            if not p.stdin.closed:
                try:
                    p.stdin.close()
                except OSError:
                    pass

    def locked_status(self):
        return (f"会话已锁定：{self.stop_reason}。已显示实时预测 {self.predictions_shown} 次。"
                "停稳未确认；有危险请使用实体急停。")

    def read(self, side, process):
        ended = False
        try:
            for line in process.stdout:
                msg = json.loads(line)
                if msg["kind"] == "feedback":
                    with self.guard:
                        self.samples[side].append((msg["stamp"], msg["q"]))
                else:
                    self.events.put((side, msg))
                    ended = msg["kind"] in ("disabled", "unconfirmed")
        except (ValueError, KeyError, OSError) as exc:
            self.events.put((side, {"kind":"fault", "reason":str(exc)}))
        finally:
            process.wait()
            if not ended:
                self.events.put((side, {"kind":"unconfirmed", "reason":"feedback process exited"}))

    def at(self, anchor):
        with self.guard:
            arms = {s: next((v for v in reversed(self.samples[s]) if v[0] <= anchor), None)
                    for s in SIDES}
        if any(v is None or anchor-v[0] >= 100_000_000 for v in arms.values()):
            raise ValueError("missing/stale arm feedback")
        return arms

    def check_watchdog(self):
        if self.opened_at is None or self.locked.is_set() or time.monotonic()-self.opened_at < 2:
            return
        now = time.time_ns()
        with self.guard:
            for s, samples in self.samples.items():
                if not samples or not 0 <= now-samples[-1][0] < 250_000_000:
                    self.stop(f"{s} 反馈中断（看门狗）")
                    self.events.put((s, {"kind":"fault", "reason":"反馈中断，已请求双臂失能"}))
                    return


def predict_live(session, path, policy=None, on_prediction=None, on_observation=None):
    import cv2
    import numpy as np
    import torch
    from shadow import ShadowPolicy
    from shadow_inputs import PERIOD_NS, VIEWS, pack_snapshot
    sys.path.insert(0, str(HERE.parent/"four_camera"))
    from bridge import Client
    from contextlib import closing

    if policy is None:
        policy = ShadowPolicy(RELEASE.checkpoint, "cuda", True, release=RELEASE)
    rng = torch.Generator(device="cuda").manual_seed(724)
    with closing(Client(path)) as camera:
        prev = None
        anchor = time.time_ns()+PERIOD_NS
        while not session.locked.is_set():
            if session.locked.wait(max(0,(anchor-time.time_ns())/1e9)):
                return
            status, frames = camera.request(f"dp_snapshot {anchor}")
            if status.get("dp_host_anchor_ns") != anchor:
                raise ValueError("相机服务未确认 DP 取帧时刻，请使用新版四路服务")
            if status.get("mock") is not False or not status.get("ready") or not status.get("basic_image_ready") or status.get("snapshot_error"):
                raise ValueError(f"physical camera snapshot not ready: {status.get('snapshot_error') or status.get('error') or 'health/image gate'}")
            cams = {}
            for f in frames:
                m = f.metadata
                k = m["camera"]
                sources = {"global":"camera_port_1", "chest":"camera_port_2_4",
                           "wrist_left":"wrist_port_2_3","wrist_right":"wrist_port_2_2"}
                if (k not in VIEWS or k in cams or m.get("source_camera") != sources[k] or
                    m.get("image_transform") != ("identity-v1" if k=="chest" else "rot180-v1") or
                    m.get("transform_applied_at") != "four-camera-service-before-store"):
                    raise ValueError("unexpected camera role/orientation")
                bgr = cv2.imdecode(np.frombuffer(f.image,np.uint8),cv2.IMREAD_COLOR)
                if bgr is None:
                    raise ValueError("bad JPEG")
                cams[k] = m["timestamp_ns"], bgr
            arms = {s:(t,np.asarray(q,np.float32)) for s,(t,q) in session.at(anchor).items()}
            x, q = pack_snapshot(anchor,cams,arms)
            if on_observation is not None:
                on_observation(anchor,frames,q,arms)
            if prev is not None:
                ts = np.asarray([prev[0],anchor],np.int64)
                if time.time_ns()-anchor >= PERIOD_NS:
                    raise ValueError("missed inference deadline")
                pred = policy.predict(ts,np.stack([prev[1],x]),np.stack([prev[2],q]),rng)
                if time.time_ns()-anchor >= PERIOD_NS:
                    raise ValueError("expired prediction")
                if not session.locked.is_set():
                    if on_prediction is not None:
                        on_prediction(anchor, pred[0], q)
                        prev = anchor,x,q
                        anchor += PERIOD_NS
                        continue
                    session.events.put(("policy",{"kind":"prediction","text":
                        f"实时预测（未执行）  右 J4 反馈 {q[11]:.3f} / 首步目标 {pred[0,11]:.3f} rad"}))
            prev = anchor,x,q
            anchor += PERIOD_NS


def panel():
    root = tk.Tk()
    root.title("OpenArm V2 · 真机部署验证（无运动）")
    session = LiveSession()
    def label(text):
        v = tk.StringVar(value=text)
        tk.Label(root,textvariable=v,justify="left",wraplength=600).pack(anchor="w",padx=16,pady=5)
        return v
    label("真机输入 → DP 预测；不使能、不发送目标。软件失能不能替代实体急停。")
    label("开始前：两臂已支撑、未使能，实体急停可及，CAN 无其他控制进程。故障或关闭也会请求失能。")
    camera = tk.Entry(root,width=65)
    camera.pack(padx=16)
    camera.insert(0, str(Path.home()/".cache/openarm-dp-field/camera.sock"))
    label("输入 VERIFY 确认允许查询两臂状态，以及在停止或故障时发送失能请求：")
    word = tk.Entry(root)
    word.pack()
    status = label("待机：尚未访问设备")
    ack = {s:label(s+"：未连接") for s in SIDES}
    results = set()
    def stop(reason="操作员请求停止"):
        first = not session.locked.is_set()
        session.stop(reason)
        start.configure(state="disabled")
        status.set(session.locked_status())
        if first:
            for s in session.workers:
                if s not in results:
                    ack[s].set(s+"：失能请求中，未确认")
    def start_session():
        path = Path(camera.get())
        if word.get() != "VERIFY" or not path.is_socket():
            status.set("未启动：请先确认相机服务套接字存在，并输入 VERIFY")
            return
        start.configure(state="disabled")
        def policy_worker():
            try:
                predict_live(session,path)
            except Exception as exc:
                session.events.put(("policy",{"kind":"fault","reason":str(exc)}))
        try:
            session.connect()
            threading.Thread(target=policy_worker,daemon=True).start()
            status.set("连接中；等待真实相机与两臂新回包")
        except Exception as exc:
            stop("启动失败："+str(exc))
    start = tk.Button(root,text="连接真机并验证（不运动）", command=start_session)
    start.pack(fill="x",padx=16)
    tk.Button(root,text="双臂软件急停并锁定  [Esc]",command=stop,bg="#aa2025",fg="white",
              font=("",18,"bold")).pack(fill="x",padx=16,pady=10)
    def poll():
        session.check_watchdog()
        if session.locked.is_set():
            status.set(session.locked_status())
        while True:
            try: side,msg = session.events.get_nowait()
            except Empty: break
            kind = msg["kind"]
            if kind=="prediction" and not session.locked.is_set():
                session.predictions_shown += 1
                status.set(msg["text"])
                for s in SIDES:
                    ack[s].set(s+"：本轮已取得 8 轴未使能反馈")
            elif kind in ("fault","unconfirmed","disabled"):
                stop(f"{side}："+msg.get("reason", "收到失能反馈，已结束本次验证"))
                if side in ack:
                    ack[side].set(side+"："+("8 轴新回包报告未使能（非停稳证明）" if kind=="disabled"
                                  else "未确认："+msg.get("reason","")))
                    if kind in ("disabled","unconfirmed"): results.add(side)
        root.after(25,poll)
    def close():
        stop("操作员关闭窗口")
        if any(p.poll() is None for p in session.workers.values()):
            status.set("已请求两臂失能，等待响应；状态未知时请使用实体急停")
            return
        root.destroy()
    root.bind("<Escape>",lambda e:stop())
    root.protocol("WM_DELETE_WINDOW",close)
    poll()
    root.mainloop()


if __name__=="__main__":
    panel()
