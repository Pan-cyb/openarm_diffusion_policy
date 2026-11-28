"""Hardware-free checks of the real graph's shared arm command gate."""
from pathlib import Path
import unittest

import pyarrow as pa
import yaml

from arm_command_mux import ArmCommandGate
from software_stop_arm import StopLatch


class GateTests(unittest.TestCase):
    def test_session_latch_and_ordinary_pause(self):
        gate, sent = ArmCommandGate(), []
        def push(source, action):
            event = {"type": "INPUT", "id": source, "value": pa.array([action])}
            gate.forward(event, lambda _, arr, meta: sent.append((arr[0].as_py(), meta)))
        push("vr", "stop")
        push("ui", "start")
        push("soft_stop", "soft_stop")
        push("vr", "start")
        push("ui", "start")
        push("soft_stop", "start")
        push("vr", "stop")
        push("soft_stop", "soft_stop")
        self.assertEqual(sent, [("stop", {}), ("start", {})] +
                         [(v, {"software_stop_latched": True}) for v in ("soft_stop", "stop", "soft_stop")])

    def test_stop_reaches_both_driver_command_ports(self):
        local = Path(__file__).resolve().parent
        graph = yaml.safe_load((local/"dataflow_webxr_real_three_camera.yaml").read_text())
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertIn("soft_stop", nodes["ui"]["outputs"])
        self.assertEqual(nodes["arm-command-mux"]["inputs"]["soft_stop"], "ui/soft_stop")
        for consumer in ("ui", "robot"):
            self.assertEqual(nodes[consumer]["inputs"]["arm_command"], "arm-command-mux/arm_command")
        module = yaml.safe_load((local/nodes["robot"]["module"]).read_text())
        arms = {node["id"]: node for node in module["nodes"]}
        for side in ("left", "right"):
            self.assertEqual(arms[side]["inputs"]["command"], "_mod/arm_command")
            self.assertIn("software_stop_arm.py", arms[side]["args"])

    def test_official_stop_uses_disable_without_parking_on_latch(self):
        from unittest.mock import Mock, patch
        import openarm_driver as drv
        latch = StopLatch()
        config = latch.config_type(drv.Config)("openarm_pedestal")
        self.assertTrue(config.get_stop_config()["moves"])
        ev = lambda key, v: {"type": "INPUT", "id": key, "value": pa.array([v])}
        events = list(latch.events([ev("command", "soft_stop"), ev("move_position", 1),
                                    ev("command", "start"), ev("command", "stop")]))
        self.assertEqual([e["value"][0].as_py() for e in events], ["stop", "stop"])
        # Never construct a driver (its constructor opens CAN).
        arm = object.__new__(drv.SingleArmDriver)
        arm.config, arm._safety_stop_reason = config, None
        arm.openarm, arm.set_latest_state, arm.smooth_move = Mock(), Mock(), Mock()
        with patch("time.sleep"):
            arm.stop()
        arm.smooth_move.assert_not_called()
        arm.openarm.disable_all.assert_called_once_with()

    def test_adapter_runs_official_event_loop_with_fake_transport_and_driver(self):
        import sys
        from unittest.mock import Mock, patch
        import dora_openarm.main as official
        import software_stop_arm
        packets = [{"type": "INPUT", "id": key, "value": pa.array([value]), "metadata": {}}
                   for key, value in (("command", "start"), ("command", "soft_stop"),
                                      ("command", "start"), ("move_position", 1))]
        class FakeNode:
            def __iter__(self):
                return iter(packets)
            def send_output(self, *args):
                pass
        observed_stop_moves = []
        def make_mock(name, config, **kwargs):
            observed_stop_moves.append(bool(config.get_stop_config()["moves"]))
            arm = Mock()
            arm.stop.side_effect = lambda: observed_stop_moves.append(bool(config.get_stop_config()["moves"]))
            return arm
        with patch.object(sys, "argv", ["arm", "--side", "left", "--config", "openarm_pedestal"]), \
             patch.object(official, "dora", official.dora), \
             patch.object(official, "openarm_driver", official.openarm_driver), \
             patch.object(official.dora, "Node", FakeNode), \
             patch.object(official.openarm_driver, "SingleArmDriver", side_effect=make_mock) as ctor:
            software_stop_arm.main()
        self.assertEqual(ctor.call_count, 1)
        self.assertEqual(observed_stop_moves, [True, False])
