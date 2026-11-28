"""No USB/CAN is opened. Exercise the actual field-session state and stop paths."""
from io import StringIO
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from live_arm_probe import before_refresh, fresh, stop_probe, open_probe, run
from live_verify import LiveSession


class FakeBus:
    def __init__(self, missing=False, code=0, malformed=False):
        # The SDK exposes a reference to each motor's mutable stats, not a copy.
        self.stats = [SimpleNamespace(responses=10, malformed_frames=0) for _ in range(8)]
        self.missing = missing
        self.code = code
        self.malformed = malformed
        self.commands = []

    def get_arm(self): return self
    def get_link_stats(self, i):
        return self.stats[i]
    def refresh_all(self): self.commands.append("refresh")
    def disable_all(self): self.commands.append("disable")
    def recv_all(self, timeout):
        for i, stats in enumerate(self.stats):
            if not (self.missing and i == 5):
                stats.responses += 1
        if self.malformed:
            self.stats[3].malformed_frames += 1
    def get_motors(self): return [self]*8
    def get_error_code(self): return self.code
    def get_position(self): return .2
    def is_link_running(self): return True
    def is_bus_healthy(self): return True


class FieldTests(unittest.TestCase):
    def test_official_start_refresh_consumed_without_resending(self):
        for fault in ("", "missing", "malformed", "unhealthy", "disabled"):
            with self.subTest(fault=fault):
                bus = FakeBus(code=int(fault != "disabled"), missing=fault=="missing",
                              malformed=fault=="malformed")
                bus.is_bus_healthy = lambda: fault != "unhealthy"
                pending = before_refresh([(bus, 8)])
                bus.refresh_all()
                bus.recv_all(500)
                if fault:
                    with self.assertRaises(ValueError):
                        fresh(bus, [0.]*8, expected_code=1, pending=pending)
                else:
                    self.assertEqual(fresh(bus,[0.]*8,expected_code=1,pending=pending)["q"],[.2]*8)
                self.assertEqual(bus.commands, ["refresh"])

    def test_query_and_stop_only(self):
        bus = FakeBus()
        self.assertEqual(fresh(bus,[0.]*8)["q"],[.2]*8)
        stop_probe(bus,[0.]*8)
        self.assertEqual(bus.commands,["refresh","disable","refresh"])

    def test_no_fresh_ack_or_enabled_motor_is_not_stopped(self):
        for bus in (FakeBus(True), FakeBus(code=1)):
            with self.assertRaises(ValueError):
                stop_probe(bus,[0.]*8)

    def test_changed_malformed_counter_not_accepted_as_feedback(self):
        with self.assertRaisesRegex(ValueError, "malformed"):
            fresh(FakeBus(malformed=True),[0.]*8)

    def test_registration_link_and_socket_faults_reject_disabled_feedback(self):
        from openarm_can import BusStatus
        bus = FakeBus()
        bus.get_motors = lambda: [bus]*7
        with self.assertRaisesRegex(ValueError, r"注册数量不符：实际 \[7\]"):
            stop_probe(bus, [0.]*8)

        bus = FakeBus()
        bus.is_link_running = lambda: False
        with self.assertRaisesRegex(ValueError, "running=False，SDK healthy=True"):
            stop_probe(bus, [0.]*8)

        bus = FakeBus()
        bus.is_bus_healthy = lambda: False
        healthy = BusStatus()
        state = SimpleNamespace(**{k: getattr(healthy, k) for k in dir(healthy)
                                   if not k.startswith("_")})
        state.write_no_buffer = SimpleNamespace(count=2)
        state.last_write_errno = 105
        state.clear = Mock()
        bus.get_bus_status = lambda: state
        with self.assertRaisesRegex(ValueError, "healthy=False；write_no_buffer=2；last_write_errno=105"):
            stop_probe(bus, [0.]*8)
        self.assertEqual(bus.commands, ["disable", "refresh"])
        state.clear.assert_not_called()

    def test_mixed_status_preserves_axis_code_and_snapshot(self):
        bus = FakeBus()
        codes = [1,1,1,9,1,1,1,0]
        bus.get_motors = lambda: [Mock(get_error_code=lambda c=c:c, get_position=lambda: .2) for c in codes]
        with self.assertRaises(ValueError) as caught:
            fresh(bus,[0.]*8,expected_code=1)
        msg = str(caught.exception)
        for detail in ("1(ENABLED)", "J4=9(UNDERVOLTAGE)", "夹爪(J8)=0(DISABLED)",str(codes)):
            self.assertIn(detail,msg)
        self.assertEqual(bus.commands,["refresh"])

    def test_init_uses_no_mode_writes_or_enable(self):
        import openarm_can
        with patch.object(openarm_can, "OpenArm") as ctor:
            for side,interface in (("left","can1"),("right","can0")):
                bus,_ = open_probe(side)
                ctor.assert_called_with(interface,True)
                self.assertEqual(bus.init_arm_motors.call_args.args[3],[])
                self.assertEqual(bus.init_arm_motors.call_args.args[1],list(range(1,9)))
                self.assertEqual([c[0] for c in bus.method_calls],
                                 ["init_arm_motors","set_callback_mode_all"])
                bus.reset_mock()

    def test_stop_pipe_reaches_both_without_policy(self):
        s = LiveSession()
        s.workers = {side: SimpleNamespace(stdin=StringIO()) for side in ("left","right")}
        s.stop()
        self.assertTrue(all(p.stdin.closed for p in s.workers.values()))
        with self.assertRaises(RuntimeError): s.connect()
        for bus, expected in [(FakeBus(),"disabled"),(FakeBus(True),"unconfirmed")]:
            with patch("live_arm_probe.open_probe", return_value=(bus,[0.]*8)), \
                 patch("live_arm_probe.select.select",return_value=([1],[],[])), \
                 patch("live_arm_probe.emit") as send:
                run("left")
                self.assertEqual(send.call_args.args[0],expected)
                self.assertIn("disable",bus.commands)

    def test_watchdog_and_no_stale_feedback(self):
        s = LiveSession()
        s.opened_at = time.monotonic()-5
        s.samples["left"].append((time.time_ns(),[0.]*8))
        s.check_watchdog()
        self.assertTrue(s.locked.is_set())
        s.stop("收到双臂失能反馈")
        self.assertIn("right 反馈中断（看门狗）", s.locked_status())
        self.assertIn("已显示实时预测 0 次", s.locked_status())
        with self.assertRaises(ValueError): s.at(time.time_ns())
