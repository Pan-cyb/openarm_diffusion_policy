"""Opt-in four-camera leader: native Quest world-space hand deltas, never HMD-relative."""

import argparse
import json
import socket
import struct
import time

import dora
import numpy as np
import pyarrow as pa
from dora_openarm_kinematics.ik import build_qpos_output
from dora_openarm_vr.quest_receiver import _map_trigger_to_gripper

from neck_sim import HandMapping, ModelProbe, SIDES, TIMEOUT
from hand_monitor import ButtonFeed, sample
from four_camera.hand_start import PROFILE


class DeltaLeader:
    def __init__(self, send, start_profile="official"):
        self.send = send
        self.start_profile = start_profile
        self.model = ModelProbe(start_profile)
        self.mapping = HandMapping(self.model.home)
        self.enabled = False
        self.latched = False
        self.fresh = False
        self.prev_x = self.prev_b = None
        self.a_since = None
        self.a_ready = False
        self.start_stamp = 0
        self.needs_open_triggers = True
        self.home_stopped = set()
        self.retry_label = ""
        self.retry_not_before = 0

    def emit(self, name, value, stamp):
        self.send(name, value, {"timestamp": stamp})

    def invalidate(self, stamp, reason="输入或控制状态改变"):
        if self.fresh:
            print(f"hand-delta: 暂停输出目标：{reason}", flush=True)
            self.emit("vr_receive_times", pa.array([0], type=pa.int64()), stamp)
        self.fresh = False
        self.a_ready = False
        self.a_since = self.prev_x = None
        self.mapping.reset("re-align with Y")

    def arm(self, command, stamp):
        self.invalidate(stamp)
        self.latched |= command == "soft_stop"
        self.enabled = command == "start" and not self.latched
        if self.enabled:
            self.home_stopped.clear()
            self.start_stamp = stamp
            self.needs_open_triggers = True
            # A new driver start goes through its home/trigger alignment again.
            self.model = ModelProbe(self.start_profile)
            self.mapping = HandMapping(self.model.home)

    def retry_stop(self, side, stamp, reason="home_miss"):
        self.retry_label = "遥操目标中断" if reason == "input_timeout" else "宽起点未到"
        self.home_stopped.add(side)
        self.retry_not_before = stamp
        self.enabled = False
        self.invalidate(stamp)  # A held during shutdown must first be released.

    def tick(self, now, stamp):
        if self.mapping.last_rx is not None and now - self.mapping.last_rx >= TIMEOUT:
            self.invalidate(stamp, "手柄输入到达超时")
            self.mapping.expire(now)

    def packet(self, p, now, stamp):
        if not isinstance(p, dict):
            self.invalidate(stamp)
            return
        if p.get("b") == 1:
            if self.prev_b is not True:
                self.emit("arm_command", pa.array(["stop"]), stamp)
            self.prev_b = True
            self.enabled = False
            self.invalidate(stamp)
            return
        self.prev_b = False
        if stamp < self.retry_not_before or (self.enabled and stamp < self.start_stamp):
            return
        triggers = [p.get(k) for k in ("rt", "lt")]
        if (any(type(v) not in (int, float) or not 0 <= v <= 1 for v in triggers)
                or any(p.get(k) not in (0, 1) for k in ("a", "x"))):
            self.invalidate(stamp)
            return
        self.tick(now, stamp)
        if self.enabled and self.needs_open_triggers and max(triggers) > 0.05:
            self.invalidate(stamp)
            return
        targets = self.mapping.update(p, now)
        if self.mapping.status.startswith("UDP received; rejected"):
            self.invalidate(stamp, self.mapping.status)
            return
        if not p["a"]:
            self.a_since, self.a_ready = None, True
        elif (self.a_ready and not self.enabled and not self.latched
              and len(self.home_stopped) != 1):
            if self.a_since is None:
                self.a_since = now
            elif now - self.a_since >= 1.5:
                self.emit("operator", pa.array(["arm_start"]), stamp)
                self.a_ready = False
        if targets is None or not self.enabled:
            if self.fresh:
                self.invalidate(stamp, self.mapping.status)
            self.prev_x = None
            return
        self.needs_open_triggers = False
        for s, trigger in zip(SIDES, triggers):
            self.model.kin.set_target(s, targets[s])
            self.model.kin.set_gripper(s, _map_trigger_to_gripper(trigger, s))
        q = self.model.kin.solve()
        if q is None or not np.isfinite(q).all():
            self.invalidate(stamp, "IK 未给出有效关节目标")
            return
        solved = time.time_ns()
        if not 0 <= solved - stamp < int(TIMEOUT * 1e9):
            self.invalidate(solved, "求解完成时输入已过期")
            return
        self.emit("position_right", build_qpos_output(q[:8]), solved)
        self.emit("position_left", build_qpos_output(q[8:]), solved)
        self.emit("vr_receive_times", pa.array([stamp], type=pa.int64()), stamp)
        self.fresh = True
        if self.prev_x is False and p["x"]:
            self.emit("operator", pa.array(["toggle"]), stamp)
        self.prev_x = bool(p["x"])


def main():
    # Linux x86_64 SO_TIMESTAMPNS: use kernel arrival time, not Dora dequeue
    # time. An old UDP backlog must not acquire fresh robot-command timestamps.
    timestampns = getattr(socket, "SO_TIMESTAMPNS", 35)
    timespec = struct.Struct("@ll")
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-profile", choices=("official", PROFILE), default="official")
    args = parser.parse_args()
    node = dora.Node()
    leader = DeltaLeader(node.send_output, args.start_profile)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, timestampns, 1)
        sock.bind(("10.42.0.1", 5006))
        sock.setblocking(False)
        peer = None
        latest = None
        buttons = ButtonFeed()
        next_display = 0.
        input_state = "等待 Quest UDP 包"
        last_logged = None
        last_input = None
        for event in node:
            if event["type"] != "INPUT":
                continue
            if event["id"] == "arm_state":
                leader.arm(event["value"][0].as_py(), time.time_ns())
                continue
            if event["id"] in ("status_left", "status_right"):
                if (event["metadata"].get("home_retry") is True
                        and event["value"][0].as_py() == "stopped"):
                    leader.retry_stop(event["id"].removeprefix("status_"), time.time_ns(),
                                      event["metadata"].get("retry_reason"))
                continue
            now, wall = time.monotonic(), time.time_ns()
            leader.tick(now, wall)
            if now >= next_display:
                link = ("Quest 数据流中断" if input_state == "已收到 Quest 手柄包"
                        and last_input is not None and now - last_input >= TIMEOUT else input_state)
                if link != last_logged:
                    print(f"hand-delta UDP: {link}", flush=True)
                    last_logged = link
                note = ("软件停止已锁定" if leader.latched else
                        leader.retry_label + "；两臂已确认失能。确认路径净空后，松开再长按 A 1.5 秒" if len(leader.home_stopped) == 2 else
                        leader.retry_label + "；等待另一侧停止状态，暂不能重试" if leader.home_stopped else
                        "机械臂输入未启用" if not leader.enabled else None)
                frame = sample(leader.mapping, leader.model.home, latest, leader.fresh, now, note)
                frame["buttons"] = buttons.snapshot()
                frame["link"] = link
                node.send_output("hand_monitor", pa.array([json.dumps(frame)]))
                next_display = now + .1
            if event["id"] != "tick":
                continue
            try:
                raw, anc, flags, source = sock.recvmsg(8192, socket.CMSG_SPACE(timespec.size))
            except BlockingIOError:
                continue
            pair = next((timespec.unpack_from(v) for lvl, typ, v in anc
                         if (lvl, typ) == (socket.SOL_SOCKET, timestampns)
                         and len(v) >= timespec.size), None)
            stamp = pair[0] * 10**9 + pair[1] if pair else 0
            age = (time.time_ns() - stamp) / 1e9
            if flags or not 0 <= age < TIMEOUT:
                input_state = "UDP 包被拒绝：截断、无时间戳或到达已过期"
                leader.invalidate(wall)
                continue
            try:
                p = json.loads(raw)
            except (UnicodeError, ValueError):
                p = None
            if peer is not None and source != peer:
                # A different sender must not inherit the previous operator's
                # anchors. Bind the session to the original sender endpoint.
                input_state = f"UDP 来源已变化：{source}，本会话仍绑定 {peer}，新来源未接受"
                continue
            leader.packet(p, time.monotonic() - age, stamp)
            last_input = time.monotonic() - age
            input_state = ("已收到 Quest 手柄包" if isinstance(p, dict)
                           and any(k in p for k in "xyab") else "收到 UDP，但没有有效的手柄按键字段")
            buttons.observe(p, stamp / 1e9)
            for key, pressed_at in buttons.edges.items():
                if pressed_at == stamp / 1e9 and buttons.down.get(key):
                    print(f"hand-delta UDP: physical {key.upper()} down (not command acknowledgement)", flush=True)
            latest = p
            if peer is None and leader.mapping.last_t is not None:
                peer = source


if __name__ == "__main__":
    main()
