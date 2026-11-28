"""Quest controller-only experiment. Outputs model targets/FK, never arm commands."""

import argparse
import json
import math
from pathlib import Path
import socket
import time
from contextlib import nullcontext

import numpy as np
import mujoco
from scipy.spatial.transform import Rotation

from dora_openarm_vr.quest_receiver import _R_FRAME, parse_lh_to_rh, pose_to_array
from openarm_control.config import ARM_JOINT_VELOCITY_LIMITS_RAD_S, ArmSetup
from openarm_control.ik_params import IKParams
from openarm_control.kinematics import Kinematics
from hand_monitor import ButtonFeed, local_server, open_desktop_panel, sample
from four_camera.hand_start import PROFILE, positions


MODEL = (Path(__file__).resolve().parents[2] /
         "openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml")
SIDES = ("right", "left")
TIMEOUT = 0.20


def orientation(pose):
    return Rotation.from_quat(pose[[4, 5, 6, 3]])


class HandMapping:
    def __init__(self, home):
        self.last_targets = {s: home[s].copy() for s in SIDES}
        self.last_rx = self.last_t = None
        self.reset("waiting for first UDP packet")

    def reset(self, status):
        self.status = status
        self.anchors = self.previous = None
        self.released = False

    def expire(self, now):
        if self.last_rx is not None and now - self.last_rx >= TIMEOUT:
            self.reset("input timeout")
            self.last_rx = self.last_t = None

    def update(self, packet, now):
        self.expire(now)
        self.last_rx = now
        try:
            if not isinstance(packet, dict):
                raise ValueError("not a JSON object")
            missing = [k for k in ("v", "vr", "vl", "t", "rc", "lc", "b", "y") if k not in packet]
            if missing:
                raise ValueError("missing fields: " + ", ".join(missing))
            flags = tuple(packet[k] for k in ("v", "vl", "vr"))
            if flags != (0, 0, 0):
                raise ValueError("tracking overall/left/right = %s/%s/%s (0 OK, 1 stale, 2 invalid)" % flags)
            t = float(packet["t"])
            if not math.isfinite(t):
                raise ValueError("invalid source timestamp")
            if self.last_t is not None and t <= self.last_t:
                if t < self.last_t:
                    # A sender restart resets its monotonic clock. Disarm first;
                    # the next stream still needs a new release/press sequence.
                    self.last_t = None
                raise ValueError("old source frame")
            if self.last_t is not None and t - self.last_t >= TIMEOUT:
                self.reset("source gap")
            hands = {}
            for s, k in zip(SIDES, ("rc", "lc")):
                try:
                    p, r = parse_lh_to_rh(packet[k])
                    if not (np.isfinite(p).all() and np.isfinite(r.as_quat()).all()):
                        raise ValueError("non-finite")
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"invalid {s} controller pose") from exc
                hands[s] = p, r
            if any(packet[k] not in (0, 1) for k in ("b", "y")):
                raise ValueError("invalid buttons")
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self.reset(f"UDP received; rejected: {exc}")
            return None
        self.last_t = t
        if packet["b"]:
            self.reset("B: disarmed")
            return None
        pressed = bool(packet["y"])
        if self.anchors is None:
            if not pressed:
                self.released = True
            if not (self.released and pressed):
                self.status = ("tracking OK; tap Y to align" if self.released
                               else "tracking OK; release Y first")
                return None
            # Arms side by side at equal reach: left-to-right defines horizontal
            # heading independently of the headset's tilted or moving frame.
            dx, _, dz = hands["right"][0] - hands["left"][0]
            if math.hypot(dx, dz) < 0.15:
                self.reset("separate hands before Y")
                return None
            self.axes = _R_FRAME * Rotation.from_euler("y", math.atan2(dz, dx))
            self.anchors = hands
            self.origin_targets = self.last_targets
            self.status = "active"
        if self.previous is not None:
            for s, (p, r) in hands.items():
                old_p, old_r = self.previous[s]
                if (np.linalg.norm(p - old_p) > 0.10 or
                        (r * old_r.inv()).magnitude() > math.radians(45)):
                    self.reset("pose jump: re-align")
                    return None
        self.previous = hands
        targets = {}
        for s, (p, r) in hands.items():
            p0, r0 = self.anchors[s]
            h = self.origin_targets[s]
            targets[s] = pose_to_array(
                h[:3] + self.axes.apply(p - p0),
                self.axes * (r * r0.inv()) * self.axes.inv() * orientation(h),
            )
        self.last_targets = targets
        return targets


class ModelProbe:
    def __init__(self, start_profile=PROFILE):
        setup = ArmSetup.from_args(str(MODEL), "bimanual", "right_ee_control_point",
                                   "site", "left_ee_control_point", "site", "home",
                                   "arm_origin", "site")
        if start_profile == PROFILE:
            q = positions()
            setup.data.qpos[:] = setup.driver_qpos_to_mujoco(
                np.array(q["right_arm"] + q["left_arm"]), base_qpos=setup.data.qpos)
            mujoco.mj_forward(setup.model, setup.data)
        elif start_profile != "official":
            raise ValueError(f"Unknown start profile: {start_profile}")
        self.home = {s: setup.read_ee_pose(s).copy() for s in SIDES}
        caps = {f"openarm_{s}_joint{i}": v for s in SIDES
                for i, v in enumerate(ARM_JOINT_VELOCITY_LIMITS_RAD_S, 1)}
        self.kin = Kinematics(setup, IKParams(velocity_limits=caps))

    def step(self, targets):
        for s in SIDES:
            self.kin.set_target(s, targets[s])
        q = self.kin.solve()
        if q is None:
            return {"ik": "no solution"}
        fk = self.kin.fk_bimanual(q[:8], q[8:])
        return {s: {"delta_mm": ((targets[s][:3] - self.home[s][:3]) * 1000).round(2).tolist(),
                    "fk_error_mm": round(float(np.linalg.norm(f[:3] - targets[s][:3]) * 1000), 3),
                    "fk_error_deg": round(math.degrees(
                        (orientation(f) * orientation(targets[s]).inv()).magnitude()), 3)}
                for s, f in zip(SIDES, fk)}


def demo(display=None):
    if display is not None:
        # A reference card should not look like a live operator or robot.
        while True:
            time.sleep(3600)
    model = ModelProbe()
    mapping = HandMapping(model.home)
    pose = dict(x=0., y=1., z=0., qx=0., qy=0., qz=0., qw=1.)
    packet = dict(v=0, vl=0, vr=0, b=False, lc=dict(pose, x=-.2),
                  rc=dict(pose, x=.2), rf=pose.copy())
    for i in range(100):
        packet.update(t=i * .01, y=i == 1)
        packet["rf"]["x"] = i * .001
        packet["rc"]["z"] = np.clip((i - 25) * .001, 0., .02)
        targets = mapping.update(packet, packet["t"])
        if targets is not None:
            result = model.step(targets)
            if i in (20, 99):
                print(json.dumps({"synthetic_frame": i, **result}))


def listen(host, display=None):
    model = ModelProbe()
    mapping = HandMapping(model.home)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, 5006))
        sock.settimeout(.05)
        if display is not None:
            open_desktop_panel(8091)
        print("Model only. Face task, hands level at equal reach. Tap Y to align; B disarms.",
              flush=True)
        last_status = None
        display_at = 0.
        packet = None
        buttons = ButtonFeed()
        result = {}
        while True:
            try:
                data, peer = sock.recvfrom(8192)
            except TimeoutError:
                mapping.expire(time.monotonic())
            else:
                if mapping.last_rx is None or time.monotonic() - mapping.last_rx >= TIMEOUT:
                    print(f"UDP received from {peer[0]}:{peer[1]} on {host}:5006", flush=True)
                try:
                    packet = json.loads(data)
                except (UnicodeError, ValueError):
                    packet = None
                now = time.monotonic()
                if display is not None:
                    buttons.observe(packet, time.time())
                targets = mapping.update(packet, now)
                if targets is not None:
                    result = model.step(targets)
                    if now >= display_at:
                        print(json.dumps(result), flush=True)
                        display_at = now + .25
            if display is not None:
                valid = mapping.status == "active" and "ik" not in result
                display[0] = dict(sample(mapping, model.home, packet, valid, time.monotonic(),
                                         "IK 无解" if mapping.status == "active" and not valid else None),
                                  buttons=buttons.snapshot())
            if last_status != mapping.status:
                print(mapping.status, flush=True)
                last_status = mapping.status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", metavar="PC_IP", help="opt in to Quest UDP input on port 5006")
    parser.add_argument("--ui", action="store_true", help="read-only desktop page on 127.0.0.1:8091")
    args = parser.parse_args()
    try:
        with (local_server("model" if args.listen else "demo") if args.ui else nullcontext()) as display:
            if args.listen:
                listen(args.listen, display)
            else:
                demo(display)
    except KeyboardInterrupt:
        pass
