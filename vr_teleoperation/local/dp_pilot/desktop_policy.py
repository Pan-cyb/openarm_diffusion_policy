"""Standalone PC policy panel. Recorded observations only; no actuator backend."""
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
import tkinter as tk


BASE = Path(__file__).resolve().parents[2] / "outputs/dp_baseline49_20261123"


def recorded_predictions(cancel):
    import numpy as np
    import torch
    from shadow import ShadowPolicy

    if cancel.is_set():
        return
    torch.set_num_threads(2)
    policy = ShadowPolicy(BASE/"run/pilot.pt", "cuda", use_graph=True)
    episode = 45
    with np.load(BASE/f"data/ep{episode}.npz", allow_pickle=False) as archive:
        ts, rgb, obs = (archive[k] for k in ("time_ns", "rgb", "obs"))
    g = torch.Generator(device="cuda").manual_seed(724+episode)
    for i in range(1, len(ts)-8):
        if cancel.is_set():
            return
        pair = slice(i-1, i+1)
        result = policy.predict(ts[pair], rgb[pair], obs[pair], g)
        if cancel.is_set():
            return
        yield f"示范 {episode} · 第 {i}/{len(ts)-9} 个窗口\n右臂 J4：反馈 {obs[i,11]:.3f} / 预测首步 {result[0,11]:.3f} rad"
        if cancel.wait(0.08):
            return


class Session:
    """Only the GUI thread changes state or accepts a result."""
    def __init__(self, source=recorded_predictions):
        self.source = source
        self.cancel = Event()
        self.messages = SimpleQueue()
        self.state = "idle"
        self.text = "未启动。机械臂不使能，不发送动作。"

    def start(self):
        if self.state != "idle":
            return
        self.state, self.text = "running", "加载本机权重，准备离线推理…"
        Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            for result in self.source(self.cancel):
                if self.cancel.is_set():
                    return
                self.messages.put(("running", result))
            self.messages.put(("done", "离线验证完成，没有发送动作。"))
        except Exception as exc:
            self.messages.put(("error", f"离线验证失败：{exc}"))

    def stop(self):
        self.cancel.set()
        self.state, self.text = "locked", "本次策略会话已锁定，不再接收推理结果。\n未连接电机，不能据此确认实机停止。"

    def update(self):
        while True:
            try:
                state, text = self.messages.get_nowait()
            except Empty:
                return
            if self.state == "running":
                self.state, self.text = state, text


def panel():
    root = tk.Tk()
    root.title("OpenArm V2 | DP 策略验证")
    session = Session()
    box = tk.Frame(root, padx=24, pady=20)
    box.pack(fill="both", expand=True)
    tk.Label(box, text="DP 策略验证", font=("", 18, "bold")).pack(anchor="w")
    tk.Label(box, text="离线回放 · 不使用 VR · 无电机指令出口").pack(anchor="w")
    status = tk.StringVar(value=session.text)
    tk.Label(box, textvariable=status, justify="left", anchor="w", height=3,
             wraplength=460).pack(fill="x", pady=10)
    start = tk.Button(box, text="开始离线验证", command=session.start)
    start.pack(fill="x")
    def stop(event=None):
        session.stop()
        status.set(session.text)
        start.configure(state="disabled")
    tk.Button(box, text="停止策略并锁定   [Esc]", bg="#a82125", fg="white",
              font=("", 16, "bold"), command=stop).pack(fill="x", pady=(8, 0))
    def refresh():
        session.update()
        status.set(session.text)
        start.configure(state="normal" if session.state == "idle" else "disabled")
        root.after(50, refresh)
    def close():
        stop()
        root.destroy()
    root.bind("<Escape>", stop)
    root.protocol("WM_DELETE_WINDOW", close)
    refresh()
    root.mainloop()


if __name__ == "__main__":
    panel()
