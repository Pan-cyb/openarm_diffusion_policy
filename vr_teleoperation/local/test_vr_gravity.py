"""Exercise the installed driver's actual send_position without constructing any CAN bus."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import time
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import openarm_driver

from vr_gravity import Bus, CAP, Gravity, HomeRetry, Ramp, Trial
from dp_pilot.live_arm_probe import MotorStatusMismatch
from four_camera.hand_start import driver_config


class GravityTests(unittest.TestCase):
    def test_stream_timeout_requires_disabled_feedback_not_just_a_disable_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Trial(tmp, 1, production=True, paired_state=True)
            t.owner = driver = Mock(arm_side="left_arm")
            driver._feedback.side_effect = ValueError("仍有电机使能")
            t.status("aligned")
            t.aligned_at = 0.
            event = {"type":"INPUT", "id":"other"}
            with patch("vr_gravity.time.monotonic", return_value=.999):
                self.assertIs(next(t.events([event])), event)
                driver.openarm.disable_all.assert_not_called()
            with patch("vr_gravity.time.monotonic", return_value=1.), self.assertRaisesRegex(RuntimeError, "未能确认安全停止"):
                next(t.events([{"type":"INPUT", "id":"gravity_watchdog"}]))
            self.assertTrue(t.fault.exists())
            self.assertFalse(t.retry_stop_pending)

    def test_paired_target_cannot_dispatch_expired_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            t = Trial(folder, 1, production=True, paired_state=True)
            cls = t.driver_type(openarm_driver.SingleArmDriver)
            a = t.owner = object.__new__(cls)
            a.openarm = Bus(Mock())
            a._feedback = Mock()
            t.pair_phase = "target"
            t.pair_sample = {"stamp": time.time_ns()-100_000_000, "q":[0.]*8}
            with self.assertRaisesRegex(RuntimeError, "stale feedback"):
                a.send_position([0.]*8)
            a.openarm.arm.wrapped.mit_control_all.assert_not_called()
            a.openarm.wrapped.disable_all.assert_called_once()

    def test_only_converging_endpoint_continues_to_normal_alignment(self):
        for mode in ("converging", "stuck_axis", "late"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                t = Trial(tmp, 1, production=True)
                t.home_marker("right").touch()
                now = [0.]
                def sample():
                    err = np.zeros(7)
                    err[6] = (.099 if mode == "late" else .092) - .015 * now[0]
                    if mode == "stuck_axis":
                        err[5] = .084
                    return err
                def sleep(dt):
                    now[0] += dt
                def check():
                    t.check_home("left", sample, clock=lambda: now[0], sleep=sleep)
                if mode == "converging":
                    check()
                    self.assertGreater(now[0], .4)
                    self.assertTrue(t.home_marker("left").exists())
                    self.assertFalse(t.retry_stop_pending)
                else:
                    with self.assertRaises(HomeRetry):
                        check()
                    self.assertTrue(t.home_marker("miss").exists())
                    self.assertLessEqual(now[0], .43 if mode == "stuck_axis" else 1.23)

    def test_endpoint_settles_without_resending_or_relaxing_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Trial(tmp, 1, production=True)
            t.home_marker("right").touch()
            read = Mock(side_effect=[np.full(7, e) for e in (.092, .085, .078, .077)])
            t.check_home("left", read)
            self.assertEqual(read.call_count, 4)
            for e in (.101, np.nan):
                with self.assertRaisesRegex(RuntimeError, "偏差异常"):
                    t.check_home("left", lambda: np.full(7, e))

    def test_both_enabled_before_first_move_and_check_wide_not_narrow(self):
        moves = driver_config()["start"]["moves"]
        near = np.array(moves[-1]["position"]["left_arm"])
        near[6] -= .092
        for scenario in ("ready", "miss", "right_fault", "no_buffer"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                right_waiting = threading.Event()
                release_right = threading.Event()
                dispatched = []

                class Base(openarm_driver.SingleArmDriver):
                    def set_latest_state(self, **kwargs):
                        self.latest_state = {"qpos": np.zeros(8)}

                    def _on_start(self):
                        dispatched.append(self.arm_side)
                        self.at_end = True
                        return True

                def prepare(side):
                    t = Trial(tmp, 1, production=True)
                    cls = t.driver_type(Base)
                    a = t.owner = object.__new__(cls)
                    a.arm_side, a.at_end, a.openarm = side + "_arm", False, Mock()
                    a.config = Mock()
                    a.config.get_start_config.return_value = {"moves": moves}
                    a._init_pending = object()
                    def feedback(*, expected=1, pending=None):
                        if expected == 0:
                            if pending is not None:
                                self.assertIs(pending, a._init_pending)
                                a.openarm.enable_all.assert_not_called()
                            return {"q": moves[0]["position"][a.arm_side]}
                        if not hasattr(a, "first_checked"):
                            self.assertIs(pending, a.baseline)
                            a.openarm.refresh_all.assert_called_once_with()
                            a.first_checked = True
                        else:
                            self.assertIsNone(pending)
                        if side == "right" and not right_waiting.is_set():
                            right_waiting.set()
                            if not release_right.wait(.1):
                                raise TimeoutError("test synchronization")
                            if scenario == "no_buffer":
                                raise ValueError("write_no_buffer=2")
                            code = 9 if scenario == "right_fault" else 0
                            raise MotorStatusMismatch(1, [1]*7+[code])
                        return {"q": near if (scenario == "miss" and side == "left") else
                                moves[-1]["position"][a.arm_side]}
                    a._feedback = feedback
                    a.baseline = object()
                    a.openarm.refresh_all.side_effect = lambda: self.assertIs(a._start_pending, a.baseline)
                    return a

                left, right = prepare("left"), prepare("right")
                marks = {a.openarm.get_arm(): a.baseline for a in (left,right)}
                with patch("vr_gravity.before_refresh", side_effect=lambda g: marks[g[0][0]]), \
                        ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(a.start) for a in (left,right)]
                    self.assertTrue(right_waiting.wait(.2))
                    self.assertEqual(dispatched, [])
                    release_right.set()
                    for future in futures:
                        if scenario == "ready":
                            self.assertTrue(future.result(timeout=2))
                        elif scenario == "miss":
                            self.assertFalse(future.result(timeout=2))
                        else:
                            with self.assertRaises(RuntimeError):
                                future.result(timeout=2)
                if scenario in ("right_fault", "no_buffer"):
                    self.assertEqual(dispatched, [])
                if scenario != "ready":
                    for a in (left, right):
                        a.openarm.disable_all.assert_called_once()
                if scenario == "miss":
                    self.assertFalse((Path(tmp)/"gravity-trial.fault").exists())

    def test_preflights_require_same_epoch_and_do_not_open_drivers(self):
        with tempfile.TemporaryDirectory() as folder:
            left, right = Trial(folder, 1), Trial(folder, 1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(left.await_preflight, "left")
                b = pool.submit(right.await_preflight, "right")
                a.result(timeout=3)
                b.result(timeout=3)
            left.fault.touch()
            with self.assertRaisesRegex(RuntimeError, "preflight"):
                left.await_preflight("left")

    def test_ramp_gap_and_limit(self):
        r = Ramp()
        self.assertEqual(r.step(0., 1), 0.)
        for i in range(1, 502):
            r.step(i*.02, 1)
        self.assertEqual(r.scale, 1)
        with self.assertRaises(RuntimeError):
            r.step(11, 1)
        r = Ramp()
        for i in range(501):
            r.step(i*.02, .25)
        self.assertEqual(r.scale, .25)

    def test_both_sides_model(self):
        for side, finger in (("left", .4), ("right", -.4)):
            q = [0.,0.,0.,1.57,0.,0.,0.,finger]
            g = Gravity(side)
            self.assertTrue(4 < g.torque(q)[3] < 5)
            with self.assertRaises(ValueError):
                g.torque([float("nan")]*8)

    def test_checked_dispatch_and_fault_disable(self):
        self.check_dispatch(False)

    def test_production_full_first_packet_and_alignment(self):
        self.check_dispatch(True)

    def check_dispatch(self, production):
        with tempfile.TemporaryDirectory() as folder:
            t = Trial(folder, 1, production=production)
            cls = t.driver_type(openarm_driver.SingleArmDriver)
            a = object.__new__(cls)
            t.owner = a
            a.openarm = Bus(Mock())
            a.gravity = Gravity("left")
            cfg = openarm_driver.Config("openarm_pedestal")
            a.kps, a.kds = cfg.get_default_kps(), cfg.get_default_kds()
            a.num_mit_motors, a.gripper_posforce = 7, True
            a.joint_offsets = np.ones(8)*.01
            a.gripper_posforce_limits = [1,1]
            a.set_latest_state = Mock()
            a.last_command_time_s = time.monotonic()
            a._safety_stop_reason = None
            q = [0.,0.,0.,1.57,0.,0.,0.,.4]
            result = SimpleNamespace(is_safe=False,force_stop=False,fixed_joint_positions=q)
            a.safety_checker = Mock()
            a.safety_checker.check.return_value = result
            with (Path(folder)/"log").open("w+") as a.trace:
                with patch("vr_gravity.fresh",return_value={"stamp":time.time_ns(),"q":q}):
                    a.send_position(q)
                send = a.openarm.arm.wrapped.mit_control_all
                packet = send.call_args.args[0]
                expected = np.clip(a.gravity.torque(q), -CAP, CAP)
                np.testing.assert_allclose([p.tau for p in packet], expected if production else np.zeros(7))
                np.testing.assert_allclose([p.kp for p in packet], a.kps[:7])
                np.testing.assert_allclose([p.kd for p in packet], a.kds[:7])
                if production:
                    # Waiting for Y/trigger is not an aligned stream interruption.
                    t.ramp.last -= 1.
                    with patch("vr_gravity.fresh",return_value={"stamp":time.time_ns(),"q":q}):
                        a.send_position(q)
                    np.testing.assert_allclose([p.tau for p in send.call_args.args[0]], expected)
                t.status("aligned")
                if production:
                    self.assertEqual(t.ramp.scale, 1.)
                else:
                    t.ramp.scale, t.ramp.last = .6, time.monotonic()
                t.stamp = time.time_ns()
                sample = {"stamp": t.stamp, "q": q}
                with patch("vr_gravity.fresh", return_value=sample):
                    self.assertTrue(a.send_position(np.ones(8)))
                    packet = send.call_args.args[0]
                    self.assertAlmostEqual(packet[3].q, 1.58)
                    self.assertGreater(packet[3].tau, 2.)
                    self.assertTrue(np.all(np.abs([p.tau for p in packet]) <= CAP))
                    a.trace.seek(0)
                    row = json.loads(a.trace.readlines()[-1])
                    self.assertEqual(row["q_command"], q)
                    self.assertEqual(row["tau_ff_nm"], [p.tau for p in packet])
                    if production:
                        np.testing.assert_allclose(row["tau_ff_nm"], expected)
                        self.assertEqual(row["scale"], 1.)
                    t.stamp -= 500_000_000
                    with self.assertRaisesRegex(RuntimeError, "stale VR"):
                        a.send_position(q)
                self.assertEqual(send.call_count, 3 if production else 2)
                a.openarm.wrapped.disable_all.assert_called_once()
                self.assertTrue(t.fault.exists())

    def test_peer_fault_preempts_even_without_move_event(self):
        with tempfile.TemporaryDirectory() as folder:
            t = Trial(folder, 1)
            t.owner = owner = Mock()
            t.fault.touch()
            with self.assertRaisesRegex(RuntimeError, "peer"):
                next(t.events([{"type":"INPUT", "id":"gravity_watchdog"}]))
            owner.openarm.disable_all.assert_called_once()

    def test_watchdog_tick_does_not_renew_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            t = Trial(folder, .25)
            t.owner = owner = Mock()
            t.status("aligned")
            t.aligned_at -= 1
            with self.assertRaisesRegex(RuntimeError, "no fresh"):
                next(t.events([{"type":"INPUT", "id":"gravity_watchdog"}]))
            owner.openarm.disable_all.assert_called_once()
