import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from neck_sim import HandMapping, ModelProbe, orientation


def pose(x=0., z=0., yaw=0.):
    q = Rotation.from_euler("y", yaw, degrees=True).as_quat()
    return dict(x=x, y=1., z=z, **dict(zip(("qx", "qy", "qz", "qw"), q)))


class NeckSimTest(unittest.TestCase):
    def setUp(self):
        self.model = ModelProbe()
        self.mapping = HandMapping(self.model.home)
        self.data = dict(v=0, vl=0, vr=0, b=False, y=False,
                         rc=pose(.2), lc=pose(-.2), rf=pose())
        self.t = 0.

    def frame(self, **changes):
        self.t += .01
        self.data.update(changes)
        return self.mapping.update(dict(self.data, t=self.t), self.t)

    def align(self):
        self.assertIsNone(self.frame(y=False))
        return self.frame(y=True)

    def test_moving_reference_fixed_hands(self):
        self.align()
        target = self.frame(rf=pose(x=.10, z=.30, yaw=65))
        for s in target:
            np.testing.assert_allclose(target[s], self.model.home[s], atol=1e-6)

    def test_right_hand_only_ik_and_fk(self):
        self.align()
        target = self.frame(rc=pose(.2, .02, 8))
        np.testing.assert_allclose(target["left"], self.model.home["left"], atol=1e-6)
        np.testing.assert_allclose(target["right"][:3] - self.model.home["right"][:3],
                                   [.02, 0, 0], atol=1e-6)
        angle = orientation(target["right"]) * orientation(self.model.home["right"]).inv()
        np.testing.assert_allclose(angle.as_rotvec(), [0, 0, -np.deg2rad(8)], atol=1e-6)
        for _ in range(40):
            result = self.model.step(target)
        for s in target:
            self.assertLess(result[s]["fk_error_mm"], 1.)
            self.assertLess(result[s]["fk_error_deg"], .5)

    def test_heading_from_hands_not_head(self):
        self.frame(rc=pose(0, -.2), lc=pose(0, .2))
        self.frame(y=True)
        target = self.frame(rc=pose(.02, -.2))
        np.testing.assert_allclose(target["right"][:3] - self.model.home["right"][:3],
                                   [.02, 0, 0], atol=1e-6)

    def test_tracking_loss_requires_new_edge_and_rebases_without_jump(self):
        self.align()
        before = self.frame(rc=pose(.2, .02))
        self.assertIsNone(self.frame(vl=1))
        self.assertIsNone(self.frame(vl=0, rc=pose(.3), y=True))
        after = self.align()
        for s in before:
            np.testing.assert_allclose(after[s], before[s], atol=1e-6)

    def test_timeout_jump_and_stop_disarm(self):
        self.align()
        self.t += .21
        self.assertIsNone(self.frame())
        self.align()
        self.assertIsNone(self.frame(rc=pose(.4)))
        self.align()
        self.assertIsNone(self.frame(b=True))
        self.assertIsNone(self.frame(b=False))

    def test_reject_bad_and_duplicate_packets(self):
        self.align()
        self.assertIsNone(self.mapping.update(dict(self.data, t=self.t), self.t + .001))
        self.align()
        self.assertIsNone(self.frame(lc=pose(float("nan"))))
        self.assertIsNone(self.frame(lc=None))

    def test_distinguishes_udp_tracking_and_button(self):
        self.assertIn("first UDP", self.mapping.status)
        self.assertIsNone(self.frame(vl=2))
        self.assertIn("0/2/0", self.mapping.status)
        self.assertIsNone(self.frame(vl=0, y=True))
        self.assertIn("release Y", self.mapping.status)
        self.assertIsNone(self.frame(y=False))
        self.assertIn("tap Y", self.mapping.status)
        self.assertIsNotNone(self.frame(y=True))

    def test_rejected_traffic_is_not_silence(self):
        p = dict(self.data, t=.01)
        del p["vl"]
        self.assertIsNone(self.mapping.update(p, .01))
        self.mapping.expire(.11)
        self.assertIn("missing fields: vl", self.mapping.status)
        self.mapping.expire(.25)
        self.assertEqual(self.mapping.status, "input timeout")


if __name__ == "__main__":
    unittest.main()
