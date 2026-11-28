"""Offline capture startup and same-session recovery tests."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import shlex
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import openarm_can
import openarm_driver
import yaml

import launch
from vr_gravity import CAP, Gravity, Trial


class FakeSocket:
    def __init__(self, interface, fd):
        self.enabled = False
        self.q = np.array([0., 0., 0., 1., 0., 0., 0., 0.])
        self.groups = []
        self.events = []
        self.status = openarm_can.BusStatus()

    def group(self, indices):
        stats = [SimpleNamespace(commands_sent=0, responses=0, malformed_frames=0) for _ in indices]
        motors = []
        for i in indices:
            motor = Mock(get_position=lambda i=i: self.q[i],
                         get_error_code=lambda: int(self.enabled), has_error=lambda: False)
            for key in ("velocity", "torque", "state_tmos", "state_trotor"):
                getattr(motor, "get_" + key).return_value = 0.
            motors.append(motor)
        group = Mock(get_motors=lambda: motors, get_link_stats=stats.__getitem__)
        self.groups.append(stats)
        return group

    def init_arm_motors(self, types, *args):
        self.arm = self.group(range(len(types)))
        def dispatch(params):
            assert self.enabled
            self.events.append("position")
            self.q[:7] = [p.q for p in params]
        self.arm.mit_control_all.side_effect = dispatch

    def init_gripper_motor(self, *args):
        self.gripper = self.group([7])
        self.gripper.set_position.side_effect = lambda q, **kw: self.q.__setitem__(7, q)

    def request(self, name):
        self.events.append(name)
        for group in self.groups:
            for axis in group:
                axis.commands_sent += 1

    def enable_all(self):
        self.enabled = True
        self.request("enable")

    def disable_all(self):
        self.enabled = False
        self.request("disable")

    def recv_all(self, timeout):
        for group in self.groups:
            for axis in group:
                axis.responses = axis.commands_sent

    def refresh_all(self): self.request("refresh")
    def get_arm(self): return self.arm
    def get_gripper(self): return self.gripper
    def get_bus_status(self): return self.status
    def is_bus_healthy(self): return True
    def is_link_running(self): return True
    def get_unmatched_frames(self): return {}
    def set_callback_mode_all(self, mode): pass


def rehearse_retry(args):
    import sys
    import pyarrow as pa
    import dora_openarm.main as official
    import software_stop_arm
    root = Path(args[args.index("--gravity-trial-dir") + 1])
    side = args[args.index("--side") + 1]
    buses, statuses = [], []

    class Socket(FakeSocket):
        def init_arm_motors(self, types, *rest):
            super().init_arm_motors(types, *rest)
            if len(types) == 7:
                buses.append(self)
                dispatch = self.arm.mit_control_all.side_effect
                def move(params):
                    dispatch(params)
                    if side == "left" and len(buses) == 1 and self.events.count("position") == 200:
                        self.q[6] -= .092
                self.arm.mit_control_all.side_effect = move

    class Node:
        def __iter__(self):
            def cmd(value, stamp):
                return {"type":"INPUT", "id":"command", "value":pa.array([value]),
                        "metadata":{"timestamp":stamp}}
            old = time.time_ns()
            yield cmd("start", old)
            yield cmd("start", old)  # Queued before the stop; must not open a third driver.
            yield cmd("start", time.time_ns())
            b = buses[-1]
            last = len(b.events)
            observations = len(self.observations)
            for i in range(8):
                if i == 4:
                    time.sleep(.5)
                    yield {"type":"INPUT", "id":"gravity_watchdog"}
                    assert statuses[-1] == ("aligned", False) and b.enabled
                q = b.q.copy()
                q[7] = 0.  # alignment trigger
                q[2] += .01 * (-1)**i
                target = {"type":"INPUT", "id":"move_position", "value":pa.array(q),
                          "metadata":{"timestamp":time.time_ns(), "start_epoch":2}}
                pair = [{**target, "id":"request_state"}, target]
                yield from pair if i % 2 else reversed(pair)
            assert b.events[last:] == ["refresh", "position"] * 8, b.events[last:]
            assert len(self.observations) == observations + 8
            time.sleep(1.05)
            old = time.time_ns()
            yield {"type":"INPUT", "id":"gravity_watchdog"}
            assert statuses[-1] == ("stopped", True)
            assert b.enabled is False
            yield cmd("start", old)
            assert len(buses) == 2
            yield cmd("start", time.time_ns())
            yield cmd("soft_stop", time.time_ns())

        def __init__(self):
            self.observations = []

        def send_output(self, name, value, metadata):
            if name == "status":
                statuses.append((value[0].as_py(), metadata.get("home_retry", False)))
            elif name == "state":
                self.observations.append(metadata["observation_timestamp"])

    with (patch.object(openarm_can, "OpenArm", Socket),
          patch.object(official.dora, "Node", Node), patch.object(sys, "argv", args)):
        software_stop_arm.main()
    return statuses, [b.events.count("position") for b in buses], (root/"gravity-trial.fault").exists()


class StartTest(unittest.TestCase):
    def test_real_node_stops_then_accepts_new_start_in_same_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launch.write_robot_module(root, "hand-delta", None)
            (root/"recorder-ready").touch()
            nodes = yaml.safe_load((root/"robot_real_pedestal.yaml").read_text())["nodes"]
            args = [shlex.split(n["args"]) for n in nodes if n["id"] in ("left", "right")]
            with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
                for statuses, counts, fault in pool.map(rehearse_retry, args):
                    self.assertEqual(statuses, [("stopped",False), ("stopped",True),
                                                ("started",False), ("aligned",False), ("stopped",True),
                                                ("started",False), ("stopped",False)])
                    self.assertEqual(counts, [200, 208, 200])
                    self.assertFalse(fault)

    def test_motion_socket_fault_before_enable_no_motion_or_false_disable_ack(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(openarm_can, "OpenArm", FakeSocket):
            root = Path(tmp)
            (root / "recorder-ready").touch()
            (root / "preflight-1-left").touch()
            cfg = openarm_driver.Config("openarm_pedestal")
            trial = Trial(tmp, 1, production=True)
            arm = trial.driver_type(openarm_driver.SingleArmDriver)("right_arm", cfg)
            self.addCleanup(arm.trace.close)
            bus = arm.openarm.wrapped
            bus.is_bus_healthy = lambda: False
            before = len(bus.events)
            with patch("builtins.print") as log, self.assertRaisesRegex(RuntimeError, "使能前"):
                arm.start()
            self.assertEqual(bus.events[before:], ["disable", "refresh"])
            self.assertTrue(json.loads(log.call_args.args[0])["disable_result"].startswith("unconfirmed:"))

    def test_generated_capture_drives_direct_to_wide(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(openarm_can, "OpenArm", FakeSocket):
            root = Path(tmp)
            launch.write_robot_module(root, "hand-delta", None)
            (root / "recorder-ready").touch()
            nodes = yaml.safe_load((root / "robot_real_pedestal.yaml").read_text())["nodes"]

            def run(node):
                args = shlex.split(node["args"])
                side = args[args.index("--side") + 1]
                cfg = openarm_driver.Config(args[args.index("--config") + 1])
                trial = Trial(args[args.index("--gravity-trial-dir") + 1],
                              int(args[args.index("--gravity-percent") + 1]) / 100,
                              production="--production-gravity" in args)
                arm = trial.driver_type(openarm_driver.SingleArmDriver)(side + "_arm", cfg)
                self.addCleanup(arm.trace.close)
                bus = arm.openarm.wrapped
                self.assertTrue(arm.start())
                self.assertEqual(bus.events.count("enable"), 1)
                moves = cfg.get_start_config()["moves"]
                self.assertEqual([m["name"] for m in moves], [launch.hand_start.PROFILE])
                rows = list(map(json.loads, Path(arm.trace.name).read_text().splitlines()))
                n = int(moves[0]["hz"] * moves[0]["duration"])
                self.assertEqual(len(rows), n)
                idx = bus.events.index("position")
                self.assertEqual(bus.events[idx-1], "refresh")
                # No redundant refresh between two consecutive position dispatches.
                self.assertEqual(bus.events[idx:], ["position", "refresh"] * n + ["refresh"])
                np.testing.assert_allclose(rows[-1]["q_command"], moves[0]["position"][arm.arm_side], atol=1e-5)
                g = Gravity(side)
                for row in rows:
                    self.assertEqual(row["scale"], 1.)
                    np.testing.assert_allclose(row["tau_ff_nm"], np.clip(g.torque(row["q_obs"]), -CAP, CAP))

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(run, [n for n in nodes if n["id"] in ("left", "right")]))
