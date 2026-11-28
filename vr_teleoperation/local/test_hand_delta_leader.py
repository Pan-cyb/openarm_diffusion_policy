import time
import unittest

import numpy as np
from dora_openarm_vr.quest_receiver import _map_trigger_to_gripper

from hand_delta_leader import DeltaLeader
from test_neck_sim import pose


class LeaderTest(unittest.TestCase):
    def setUp(self):
        self.outputs = []
        self.leader = DeltaLeader(lambda n, v, m: self.outputs.append((n, v[0].as_py())))
        self.p = dict(lc=pose(-.2), rc=pose(.2), rf=pose(), v=0, vl=0, vr=0,
                      a=0, b=0, x=0, y=0, lt=0., rt=0.)
        self.now = time.monotonic()

    def frame(self, **fields):
        self.now += .01
        self.p.update(t=self.now, **fields)
        self.outputs.clear()
        self.leader.packet(self.p, self.now, time.time_ns())
        return dict(self.outputs)

    def start(self):
        self.leader.arm("start", time.time_ns())
        self.frame(y=0)
        return self.frame(y=1)

    def test_hand_delta_to_official_ik_grippers_and_x(self):
        self.assertFalse(self.frame())
        home = self.start()
        out = self.frame(rf=pose(x=.3, yaw=42), rt=1., x=1)
        for s, trigger in (("left", 0.), ("right", 1.)):
            q = out["position_" + s]["qpos"]
            np.testing.assert_allclose(q[:-1], home["position_" + s]["qpos"][:-1], atol=1e-4)
            self.assertAlmostEqual(q[-1], _map_trigger_to_gripper(trigger, s), places=6)
        self.assertEqual(out["operator"], "toggle")
        self.assertNotIn("operator", self.frame(rc=pose(.2, .02)))

    def test_loss_and_stop_override_buttons(self):
        self.start()
        self.assertEqual(self.frame(vl=1)["vr_receive_times"], 0)
        self.assertNotIn("position_right", self.frame(vl=0, y=1))
        self.frame(y=0)
        self.assertIn("position_right", self.frame(y=1))
        self.assertEqual(self.frame(b=1, vl=2, x=1)["arm_command"], "stop")
        self.assertNotIn("position_right", self.frame(vl=0, b=0, y=1))
        self.leader.arm("soft_stop", time.time_ns())
        self.leader.arm("start", time.time_ns())
        self.assertFalse(self.leader.enabled)

    def test_silence_requires_reanchor_and_clears_readiness(self):
        self.start()
        self.outputs.clear()
        self.leader.tick(self.now + .21, time.time_ns())
        self.assertEqual(dict(self.outputs), {"vr_receive_times": 0})
        self.assertNotIn("position_left", self.frame())

    def test_home_retry_requires_both_stopped_and_new_long_press(self):
        self.start()
        self.leader.retry_stop("left", time.time_ns())
        self.frame(a=0)
        self.assertFalse(any(self.frame(a=1).get("operator") for _ in range(165)))
        self.leader.retry_stop("right", time.time_ns())
        self.assertFalse(any(self.frame(a=1).get("operator") for _ in range(165)))
        self.frame(a=0)
        self.assertEqual([v for _ in range(170) if (v := self.frame(a=1).get("operator"))], ["arm_start"])
        self.leader.arm("soft_stop", time.time_ns())
        self.frame(a=0)
        self.assertFalse(any(self.frame(a=1).get("operator") for _ in range(165)))
