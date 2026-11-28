import unittest

import numpy as np

from pdff_sweep import ff, gains, hold, run


class SweepTests(unittest.TestCase):
    def test_gains_and_ff_apply_to_both_seven_axis_arms(self):
        kp, kd = gains("cell100")
        for part in (slice(7), slice(7, 14)):
            np.testing.assert_equal(kp[part], [230,230,190,190,30,30,30])
            np.testing.assert_equal(kd[part], [2.7,2.7,2.2,2.2,1.5,1.5,1.5])
        g = np.arange(-7., 7.)
        for prefix in ("ped","cell"):
            for pct in (0, 50, 100):
                np.testing.assert_equal(ff(f"{prefix}{pct}", g), g*pct/100)

    def test_equal_feeds_at_same_asymmetric_initial_measurement(self):
        q = hold("bent")[:20].copy()
        q[:,0], q[:,8], q[:,3] = -.2, .1, 1.2
        a, tau = run(q, "ped100")
        b, other = run(q, "cell100")
        np.testing.assert_allclose(tau[0], other[0])
        self.assertGreater(abs(tau[0,3]-tau[0,10]), .5)
        self.assertEqual(a.shape, (20, 14))
        self.assertTrue(np.isfinite(b).all())
