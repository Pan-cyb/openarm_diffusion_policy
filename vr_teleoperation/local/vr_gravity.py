"""Pedestal gravity feedforward on the official driver's checked arm packets."""
import json
import time
from pathlib import Path

import mujoco as mj
import numpy as np

from dp_pilot.live_arm_probe import await_enable, before_refresh, fresh, open_probe
from dp_pilot.gravity_profile import CAP_NM, RAMP_S

MODEL = Path(__file__).resolve().parents[2] / "openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"
TIMEOUT = .20
HAND_DELTA_STREAM_TIMEOUT = 1.0
# Nominal joint-space experiment ceilings, not measured load or certified limits.
CAP = np.array(CAP_NM)


class Gravity:
    def __init__(self, side):
        if side not in ("left", "right"):
            raise ValueError("unknown arm")
        m = self.model = mj.MjModel.from_xml_path(str(MODEL))
        self.data = mj.MjData(m)
        joints = [m.joint(f"openarm_{side}_joint{i}") for i in range(1, 8)]
        self.pos = [j.qposadr.item() for j in joints]
        self.dof = [j.dofadr.item() for j in joints]
        self.fingers = [m.joint(f"openarm_{side}_finger_joint{i}").qposadr.item() for i in (1, 2)]

    def torque(self, q):
        q = np.asarray(q)
        if q.shape != (8,) or not np.isfinite(q).all():
            raise ValueError("invalid measured pose")
        self.data.qpos[self.pos] = q[:7]
        self.data.qpos[self.fingers] = q[7]
        mj.mj_forward(self.model, self.data)
        tau = self.data.qfrc_bias[self.dof].copy()
        if not np.isfinite(tau).all():
            raise ValueError("nonfinite model gravity")
        return tau


class Ramp:
    def __init__(self, *, immediate=False):
        self.immediate = immediate
        self.last, self.scale = None, 0.

    def step(self, now, maximum, *, gap_timeout=TIMEOUT):
        if self.last is not None:
            dt = now - self.last
            if not 0 <= dt < gap_timeout:
                raise RuntimeError("teleop command stream interrupted")
            if not self.immediate:
                self.scale = min(maximum, self.scale + min(dt, .04) / RAMP_S)
        if self.immediate:
            self.scale = maximum
        self.last = now
        return self.scale


class Proxy:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


class ArmPackets(Proxy):
    """Only modify the torque field of the checked, already constructed arm command."""
    def mit_control_all(self, params):
        if len(params) != 7 or any(p.tau != 0 for p in params):
            raise RuntimeError("unexpected MIT layout or pre-existing feedforward")
        if self.pending is not None:
            sample_ns, expected, effort = self.pending
            if not 0 <= time.time_ns() - sample_ns < 50_000_000:
                raise RuntimeError("stale joint feedback at dispatch")
            if not 0 <= time.time_ns() - expected < int(TIMEOUT*1e9):
                raise RuntimeError("stale VR target at dispatch")
            for p, tau in zip(params, effort, strict=True):
                p.tau = float(tau)
        return self.wrapped.mit_control_all(params)


class Bus(Proxy):
    def __init__(self, wrapped):
        super().__init__(wrapped)
        self.arm = ArmPackets(wrapped.get_arm())
        self.arm.pending = None

    def get_arm(self):
        return self.arm


class HomeRetry(Exception):
    """A small endpoint miss after the trajectory, not a communication fault."""


class Trial:
    def __init__(self, directory, maximum, *, production=False, paired_state=False):
        if maximum not in (0., .25, .5, 1.):
            raise ValueError("unknown gravity level")
        self.directory, self.maximum = Path(directory), maximum
        self.fault = self.directory / "gravity-trial.fault"
        self.aligned, self.stamp, self.owner = False, None, None
        self.ramp = Ramp(immediate=production)
        self.epoch = 0
        self.production = production
        self.paired_state = paired_state
        self.stream_timeout = HAND_DELTA_STREAM_TIMEOUT if production and paired_state else TIMEOUT
        self.pair_phase, self.pair_sample = None, None
        self.stream_last = None
        self.retry_stop_pending = False
        self.retry_after_ns = 0

    def await_preflight(self, side):
        # Both owners must see disabled, fresh replies before either constructs a motion driver.
        self.epoch += 1
        prefix = f"preflight-{self.epoch}-"
        (self.directory / (prefix + side)).touch(exist_ok=False)
        peer = self.directory / (prefix + {"left":"right", "right":"left"}[side])
        deadline = time.monotonic() + 2.
        while not peer.exists():
            if self.fault.exists() or time.monotonic() >= deadline:
                self.abort("missing other arm disabled preflight")
            time.sleep(.01)
        if self.fault.exists():
            self.abort("peer preflight fault")

    def check_peer(self):
        if self.fault.exists():
            raise RuntimeError("peer fault")

    def home_marker(self, label):
        return self.directory / f"home-{self.epoch}-{label}"

    def check_home(self, side, read_error, *, clock=time.monotonic, sleep=time.sleep):
        other = {"left": "right", "right": "left"}[side]
        start = clock()
        checkpoint, prior = start + .4, None
        while True:
            self.check_peer()
            if self.home_marker("miss").exists():
                raise HomeRetry("另一侧启动终点未到位")
            err = np.asarray(read_error())
            if err.shape != (7,) or not np.isfinite(err).all() or np.max(np.abs(err)) > .10:
                raise RuntimeError(f"启动终点偏差异常(rad)={err.tolist()}")
            now, distance = clock(), np.abs(err)
            outside = distance > .08
            if now - start <= 1.2 and not np.any(outside):
                break
            if prior is None:
                prior = distance
            if now - start >= 1.2 or (now >= checkpoint and
                    np.any((prior - distance)[outside] < .005)):
                self.home_marker("miss").touch()
                raise HomeRetry(f"启动终点未到位(rad)={err.tolist()}")
            if now >= checkpoint:
                prior, checkpoint = distance, now + .4
            sleep(.02)  # Observe settling; never send another position target here.
        self.home_marker(side).touch(exist_ok=False)
        deadline = clock() + 2.
        while not self.home_marker(other).exists():
            self.check_peer()
            if self.home_marker("miss").exists():
                raise HomeRetry("另一侧启动终点未到位")
            if clock() >= deadline:
                raise TimeoutError("另一侧未确认启动终点")
            sleep(.01)
        self.check_peer()
        final = np.asarray(read_error())
        if final.shape != (7,) or not np.isfinite(final).all() or np.max(np.abs(final)) > .08:
            raise RuntimeError(f"等待双臂确认期间偏离宽起点(rad)={final.tolist()}")

    def abort(self, reason):
        self.aligned = False
        owner, self.owner = self.owner, None
        status = "no_motion_owner"
        try:
            try:
                if owner is not None:
                    owner.openarm.disable_all()
            finally:
                self.fault.touch(exist_ok=True)
            if owner is not None:
                owner.openarm.recv_all(1000)
                owner._feedback(expected=0)
                status = "disabled_confirmed"
        except Exception as exc:
            status = f"unconfirmed: {exc}"
        finally:
            print(json.dumps({"event": "gravity_abort", "reason": reason,
                              "disable_result": status}, ensure_ascii=False), flush=True)
        raise RuntimeError(reason)

    def disable_for_retry(self, driver, side, reason):
        other = {"left":"right", "right":"left"}[side]
        driver.openarm.disable_all()
        driver.openarm.recv_all(1000)
        driver._feedback(expected=0)
        self.home_marker(f"{reason}-{side}-off").touch(exist_ok=False)
        deadline = time.monotonic() + 2.
        while not self.home_marker(f"{reason}-{other}-off").exists():
            self.check_peer()
            if time.monotonic() >= deadline:
                raise TimeoutError("另一侧失能未确认，禁止 A 重试")
            time.sleep(.01)
        self.check_peer()
        driver._retry_disabled = True
        self.retry_stop_pending = True

    def retry_event(self, reason):
        import pyarrow as pa
        self.retry_after_ns = time.time_ns()
        yield {"type":"INPUT", "id":"command", "value":pa.array(["stop"]),
               "metadata":{"timestamp":self.retry_after_ns, "home_retry":True, "retry_reason":reason}}
        self.retry_stop_pending = False

    def events(self, node):
        # Diagnostic graph supplies a 40 ms Dora tick independent of VR/IK.
        for event in node:
            if self.fault.exists():
                self.abort("gravity trial stopped by peer")
            if event["type"] == "STOP":
                if self.owner is not None:
                    self.abort("gravity trial graph stopped")
                return
            if event["type"] == "ERROR":
                self.abort(f"Dora error: {event}")
            overdue = self.aligned and time.monotonic() - (
                self.aligned_at if self.stream_last is None else self.stream_last) >= self.stream_timeout
            if self.paired_state and self.owner is not None and (
                    overdue or self.home_marker("input-timeout").exists()):
                self.home_marker("input-timeout").touch()
                try:
                    driver = self.owner
                    self.disable_for_retry(driver, driver.arm_side.removesuffix("_arm"), "input_timeout")
                except Exception as exc:
                    self.abort(f"遥操目标中断，未能确认安全停止：{exc}")
                print("遥操目标中断；双侧回包均确认失能，不自动恢复。", flush=True)
                yield from self.retry_event("input_timeout")
                # A simultaneous red button still has to reach the permanent stop latch.
                if (event["type"] == "INPUT" and event["id"] == "command"
                        and event["value"][0].as_py() == "soft_stop"):
                    yield event
                continue
            if overdue:
                self.abort("no fresh aligned command")
            if event["type"] == "INPUT" and event["id"] == "gravity_watchdog":
                continue
            if self.paired_state and event["type"] == "INPUT" and event["id"] == "request_state":
                # The descriptor retains the observation edge for recorder metadata;
                # its publication is paired with the corresponding move below.
                continue
            self.stamp = None
            if event["type"] == "INPUT" and event["id"] == "move_position":
                self.stamp = event["metadata"].get("timestamp", -1)
                if self.paired_state:
                    # Same original edge: publish the observation first, then handle
                    # precisely this target. Never reuse feedback across Dora events.
                    try:
                        self.pair_phase = "observation"
                        yield {**event, "id": "request_state"}
                        self.check_peer()
                        self.pair_phase = "target"
                        yield event
                    finally:
                        self.pair_phase, self.pair_sample = None, None
                    continue
            if (self.retry_after_ns and event["type"] == "INPUT" and event["id"] == "command"
                    and event["value"][0].as_py() == "start"):
                stamp = event["metadata"].get("timestamp")
                if type(stamp) is not int or stamp <= self.retry_after_ns:
                    continue
            yield event
            if self.retry_stop_pending:
                # upstream ignores start()'s False return. Drop its STARTED output
                # (in the Node adapter) and transition its real FSM through Stop.
                yield from self.retry_event("home_miss")
        if self.owner is not None:
            self.abort("gravity trial input stream closed")

    def status(self, value):
        self.aligned = value == "aligned"
        if not self.production:
            self.ramp = Ramp()
        self.stream_last = None
        if self.aligned:
            self.aligned_at = time.monotonic()

    def driver_type(self, base):
        trial = self

        class TrialDriver(base):
            def fetch_state(self, refresh=True):
                if trial.pair_phase == "observation":
                    try:
                        trial.pair_sample = self._feedback()
                        return super().fetch_state(refresh=False)
                    except Exception as exc:
                        trial.abort(str(exc))
                # The official constructor finishes with refresh/receive cycles. Reuse
                # the last cycle on the motion socket, not the closed preflight socket.
                if refresh and getattr(self, "_initializing", False):
                    self._init_pending = before_refresh(self._motor_groups())
                return super().fetch_state(refresh=refresh)

            def fetch_position(self, refresh=True):
                if trial.pair_phase == "target":
                    try:
                        return np.asarray(self._paired_feedback()["q"])
                    except Exception as exc:
                        trial.abort(str(exc))
                return super().fetch_position(refresh=refresh)

            def _paired_feedback(self):
                sample = trial.pair_sample
                if sample is None or not 0 <= time.time_ns() - sample["stamp"] < 50_000_000:
                    raise RuntimeError("missing or stale feedback for paired VR target")
                if not self.openarm.is_link_running() or not self.openarm.is_bus_healthy():
                    raise RuntimeError("CAN health changed after paired observation")
                return sample

            def _motor_groups(self):
                return [(self.openarm.get_arm(), 7), (self.openarm.get_gripper(), 1)]

            def start(self):
                stage = "使能前核对运动驱动"
                try:
                    trial.check_peer()
                    if self._init_pending is None:
                        raise RuntimeError("未取得初始化刷新计数，不使能")
                    self._feedback(expected=0, pending=self._init_pending)
                    self._init_pending = None
                    trial.check_peer()
                    self._mark_start_refresh = True
                    self._start_pending = None
                    stage = "官方启动"
                    return super().start()
                except Exception as exc:
                    if trial.owner is not None:
                        trial.abort(f"{stage}: {exc}")
                    raise

            def set_latest_state(self, *args, **kwargs):
                super().set_latest_state(*args, **kwargs)
                if getattr(self, "_mark_start_refresh", False):
                    # Official start() next calls refresh_all(), then receives its replies.
                    self._start_pending = before_refresh(self._motor_groups())
                    self._mark_start_refresh = False

            def _feedback(self, *, expected=1, pending=None):
                return fresh(self.openarm, self.joint_offsets, self._motor_groups(), expected, pending=pending)

            def _on_start(self):
                try:
                    if self._mark_start_refresh or self._start_pending is None:
                        raise RuntimeError("未取得官方启动刷新前的八轴计数；不下发回位目标")
                    def read_enable():
                        pending, self._start_pending = self._start_pending, None
                        return self._feedback(pending=pending)
                    await_enable(read_enable, trial.check_peer)
                    side = self.arm_side.removesuffix("_arm")
                    prefix = f"enabled-{trial.epoch}-"
                    (trial.directory / (prefix + side)).touch(exist_ok=False)
                    peer = trial.directory / (prefix + {"left":"right", "right":"left"}[side])
                    deadline = time.monotonic() + 1.
                    while not peer.exists():
                        trial.check_peer()
                        self._feedback()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("另一臂八轴使能未确认；不下发回位目标")
                        time.sleep(.005)
                    trial.check_peer()
                    initial = self._feedback()
                    self.last_command = np.asarray(initial["q"])
                    self.last_command_time_s = time.monotonic()
                    # The first interpolation point starts at this very observation.
                    self._first_home_sample = initial if trial.production else None
                    self._homing = True
                    try:
                        if super()._on_start() is False:
                            raise RuntimeError("official start trajectory rejected")
                    finally:
                        self._first_home_sample = None
                        self._homing = False
                    target = self.config.get_start_config()["moves"][-1]["position"][self.arm_side]
                    # Check the configured final arm pose, not the first (narrow) waypoint.
                    def error():
                        return np.asarray(self._feedback()["q"])[:7] - target[:7]
                    from four_camera.hand_start import PROFILE
                    if (trial.production and
                            self.config.get_start_config()["moves"][-1].get("name") == PROFILE):
                        try:
                            trial.check_home(side, error)
                        except HomeRetry as exc:
                            trial.disable_for_retry(self, side, "home_miss")
                            print(f"{side}: {exc}；已确认本侧失能，清场后重新长按 A。", flush=True)
                            return False
                    elif np.any(np.abs(error()) > .08):
                        raise RuntimeError("启动终点未到位")
                    return True
                except Exception as exc:
                    if trial.owner is None:  # A nested send already faulted and requested disable.
                        raise
                    trial.abort(str(exc))

            def __init__(self, name, config, **kwargs):
                side = name.removesuffix("_arm")
                self.gravity = Gravity(side)
                if (trial.fault.exists() or kwargs.get("can_interface") or not config.get_gripper_posforce()
                        or config.get_recv_ids() != list(range(0x11,0x19))):
                    raise RuntimeError("not a fresh pedestal trial/config")
                deadline = time.monotonic() + 5.
                while not (trial.directory / "recorder-ready").is_file():
                    trial.check_peer()
                    if time.monotonic() >= deadline:
                        trial.abort("Recorder 初始化未确认；不开运动驱动")
                    time.sleep(.02)
                probe, offsets = open_probe(side)
                fresh(probe, offsets)  # Same owner; require new disabled replies before opening motion driver.
                del probe
                trial.await_preflight(side)
                self.trace = (trial.directory / f"gravity-{side}-{time.time_ns()}.jsonl").open("x", buffering=1)
                trial.ramp = Ramp(immediate=trial.production)
                self._init_pending = None
                self._initializing = True
                super().__init__(name, config, **kwargs)
                self._initializing = False
                self.openarm = Bus(self.openarm)
                trial.owner = self

            def stop(self):
                # Only diagnostic sessions skip ordinary parking.
                trial.aligned = False
                if not trial.production:
                    self._safety_stop_reason = "gravity trial stop: skip parking"
                trial.stamp = None
                try:
                    if getattr(self, "_retry_disabled", False):
                        return  # Already disabled and freshly verified, never park here.
                    return super().stop()
                finally:
                    trial.owner = None
                    self.trace.close()

            def send_position(self, position):
                if trial.fault.exists():
                    trial.abort("peer fault")
                if getattr(self, "_homing", False) and trial.home_marker("miss").exists():
                    trial.abort("另一侧未到位而本侧仍在回位，停止并锁定")
                if not trial.aligned and not trial.production:
                    return super().send_position(position)
                try:
                    sample = getattr(self, "_first_home_sample", None)
                    self._first_home_sample = None
                    if trial.pair_phase == "target":
                        sample = self._paired_feedback()
                    elif sample is None:
                        sample = self._feedback()
                    stamp = trial.stamp
                    now = time.monotonic()
                    if trial.production and not trial.aligned:
                        # Homing/parking targets are generated locally, not by VR.
                        if stamp is None:
                            stamp = sample["stamp"]
                        # No command is generated while waiting for the alignment trigger.
                        # Allow this pre-alignment idle gap, not a gap in aligned control.
                        if trial.ramp.last is not None and now-trial.ramp.last >= TIMEOUT:
                            trial.ramp.last = now
                    elif trial.production and trial.stream_last is None:
                        trial.ramp.last = now
                    if type(stamp) is not int:
                        raise RuntimeError("missing IK source timestamp")
                    scale = trial.ramp.step(now, trial.maximum,
                                            gap_timeout=trial.stream_timeout if trial.aligned else TIMEOUT)
                    g = self.gravity.torque(sample["q"])
                    tau = np.clip(scale*g, -CAP, CAP)
                    self.openarm.arm.pending = sample["stamp"], stamp, tau
                    if not super().send_position(position):
                        raise RuntimeError("official driver rejected VR command")
                    if trial.aligned:
                        trial.stream_last = now
                    self.trace.write(json.dumps(dict(feedback_ns=sample["stamp"], target_ns=stamp,
                        dispatch_ns=self.last_command_dispatch_timestamp_ns, q_obs=sample["q"],
                        q_command=self.last_command.tolist(), scale=scale, tau_ff_nm=tau.tolist()),
                        allow_nan=False)+"\n")
                    return True
                except Exception as exc:
                    trial.abort(str(exc))
                finally:
                    self.openarm.arm.pending = None
        return TrialDriver
