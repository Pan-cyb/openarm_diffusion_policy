"""Offline FK checks for the added official-start -> wide-start segment."""

import math
import unittest
from importlib.resources import files

import mujoco
import numpy as np
import yaml

from four_camera.hand_start import PROFILE, driver_config, positions
from hand_delta_leader import DeltaLeader
from neck_sim import ModelProbe, SIDES, orientation


class StartTests(unittest.TestCase):
    def test_only_replaces_start_move(self):
        cfg = driver_config()
        move, = cfg.pop("start")["moves"]
        official = yaml.safe_load(files("openarm_driver").joinpath(
            "configs/openarm_pedestal.yaml").read_text())
        del official["start"]
        self.assertEqual(cfg, official)
        self.assertEqual(move["position"], positions())

    def test_driver_path_and_first_ik(self):
        cfg = driver_config()
        wide, = cfg["start"]["moves"]
        old = yaml.safe_load(files("openarm_driver").joinpath(
            "configs/openarm_pedestal.yaml").read_text())["start"]["moves"][0]
        setup = ModelProbe("official").kin.setup
        model, data = setup.model, setup.data
        self.assertFalse(model.opt.disableflags & int(mujoco.mjtDisableBit.mjDSBL_CONTACT))
        q0, q1 = (np.concatenate([move["position"][s+"_arm"] for s in SIDES])
                  for move in (old, wide))
        base = data.qpos.copy()
        def fk(q):
            data.qpos[:] = setup.driver_qpos_to_mujoco(q, base_qpos=base)
            mujoco.mj_forward(model, data)
            self.assertEqual(data.ncon, 0)
            return {s: setup.read_ee_pose(s) for s in SIDES}

        original = fk(q0)
        limits = np.vstack([cfg["joint_limits"][s+"_arm"] for s in SIDES])
        jac_p, jac_r = (np.zeros((3, model.nv)) for _ in range(2))
        count = int(wide["hz"] * wide["duration"])
        dq = (q1-q0) / (count-1)
        self.assertTrue(np.all(abs(dq) * wide["hz"] < cfg["joint_velocity_limits"] * 2))
        for t in np.linspace(0, 1, count):
            q = q0 + t * (q1-q0)
            self.assertGreater(min(np.min(q-limits[:, 0]), np.min(limits[:, 1]-q)), .1)
            current = fk(q)
            for s, sign in zip(SIDES, (-1, 1)):
                delta = current[s][:3] - original[s][:3]
                tolerance = 1e-5 if t == 1 else .002
                np.testing.assert_allclose(delta, [0, sign*.04*t, 0], atol=tolerance)
                angle = (orientation(current[s]) * orientation(original[s]).inv()).magnitude()
                self.assertLess(math.degrees(angle), .001 if t == 1 else 1.)
                mujoco.mj_jacSite(model, data, jac_p, jac_r, setup.frame_ids[s])
                dofs = setup.joint_resolver.arm_dof_indices(s)
                values = np.linalg.svd(np.vstack((jac_p[:, dofs], .3*jac_r[:, dofs])),
                                       compute_uv=False)
                self.assertGreater(values[-1]/values[0], .02)

        # Arm workers are independent: also sample the swept combinations.
        for r in np.linspace(0, 1, 11):
            for l in np.linspace(0, 1, 11):
                fk(q0 + np.repeat([r, l], 8) * (q1-q0))

        expected = fk(q1)
        leader = DeltaLeader(lambda *args: None, PROFILE)
        leader.arm("start", 0)
        for s in SIDES:
            np.testing.assert_allclose(leader.mapping.last_targets[s], expected[s], atol=1e-6)
            leader.model.kin.set_target(s, expected[s])
        first = leader.model.kin.solve()
        for offset in (0, 8):
            np.testing.assert_allclose(first[offset:offset+7], q1[offset:offset+7], atol=.001)
