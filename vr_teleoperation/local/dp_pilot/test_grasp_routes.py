"""No camera, GPU or robot needed for phase/window boundary tests."""
import unittest

import numpy as np

from grasp_routes_data import phase


class PhaseTests(unittest.TestCase):
    def example(self):
        q=np.zeros((100,16));q[:,7]=.78;q[50:,7]=.1
        return q,q.copy()

    def test_trims_leading_pause_keeps_two_frames_before_first_action(self):
        q,y=self.example()
        y[8:,12]=.08
        self.assertEqual(phase(q,y),(6,50,71))
        self.assertEqual(phase(q.copy(),y.copy()),(6,50,71))

    def test_no_cut_when_immediate_or_gripper_only_start(self):
        q,y=self.example();q[1:,15]=.08
        self.assertEqual(phase(q,y)[0],0)

    def test_capped_and_close_requires_two_samples(self):
        q,y=self.example();q[24,7]=.3
        self.assertEqual(phase(q,y),(12,50,71))
        with self.assertRaisesRegex(ValueError,"missing"):
            phase(q[:30],y[:30])
