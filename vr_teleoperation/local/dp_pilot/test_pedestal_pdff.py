"""Offline tests; no robot SDK imports."""
import unittest

import mujoco as mj
import numpy as np

from pedestal_pdff import FF_CAP_NM, Hold, PROFILES, feedforward


class PedestalTests(unittest.TestCase):
    def test_fraction_sign_cap_and_invalid(self):
        np.testing.assert_equal(feedforward([2., -1.]), [1., -.5])
        np.testing.assert_equal(feedforward([-100., 100.]), [-FF_CAP_NM, FF_CAP_NM])
        for bad in ([np.nan, 0], [0, np.inf], [2.]):
            with self.assertRaises(ValueError):
                feedforward(bad)

    def test_baseline_and_only_j4_gain_changes(self):
        base, alt = Hold("baseline", "home"), Hold("combined", "home")
        m = alt.model
        np.testing.assert_equal(base.model.actuator_gainprm[base.elbows, 0], [60., 60.])
        np.testing.assert_equal(m.actuator_gainprm[alt.elbows, 0], [80., 80.])
        for j in range(m.nu):
            if j not in alt.elbows:
                np.testing.assert_equal(base.model.actuator_biasprm[j], m.actuator_biasprm[j])
                np.testing.assert_equal(base.model.actuator_gainprm[j], m.actuator_gainprm[j])
        np.testing.assert_equal(base.model.actuator_forcerange, m.actuator_forcerange)

    def test_gravity_is_pose_potential_gradient_not_target_or_speed(self):
        sim = Hold("combined", "home")
        m, d = sim.model, sim.data
        d.qpos[sim.coords] = [.54, 1.38]  # Distinct values catch swapped sides.
        tau = sim.gravity()
        d.ctrl[sim.elbows] = 0.
        d.qvel[:] = 2.
        np.testing.assert_allclose(sim.gravity(), tau)
        def potential():
            mj.mj_forward(m, d)
            return -m.body_mass @ d.xipos @ m.opt.gravity
        for i, k in enumerate(sim.coords):
            q = d.qpos[k]
            d.qpos[k] = q + 1.e-5
            plus = potential()
            d.qpos[k] = q - 1.e-5
            minus = potential()
            d.qpos[k] = q
            self.assertAlmostEqual(tau[i], (plus-minus)/2.e-5, places=6)

    def test_two_bimanual_holds(self):
        for pose in ("home", "bent"):
            result = {p: Hold(p, pose).run() for p in PROFILES}
            err = {p: np.abs(r["mean_signed_error_deg_last_0.5s"]) for p, r in result.items()}
            for p in ("pd", "gravity"):
                self.assertTrue(np.all(err["combined"] < err[p]))
                self.assertTrue(np.all(err[p] < err["baseline"]))
            for p, r in result.items():
                peak = np.array(r["peak_abs_ff_nm"])
                if p in ("pd", "baseline"):
                    np.testing.assert_equal(peak, 0.)
                else:
                    self.assertTrue(np.all((peak > 0.) & (peak <= FF_CAP_NM)))


if __name__ == "__main__":
    unittest.main()
