import mujoco
import numpy as np

from teaching_arm.model import HOME_Q, load_arm, step_control


def test_model_and_dm4310_parameters() -> None:
    runtime = load_arm()
    assert runtime.model.nq == 2
    assert runtime.model.nu == 2
    np.testing.assert_allclose(runtime.data.qpos, HOME_Q)
    np.testing.assert_allclose(runtime.model.actuator_forcerange, [[-7, 7], [-7, 7]])
    np.testing.assert_allclose(runtime.model.dof_damping, [0.9, 0.9])
    np.testing.assert_allclose(runtime.model.dof_frictionloss, [0.1, 0.1])
    np.testing.assert_allclose(runtime.model.dof_armature, [0.002, 0.002])
    assert runtime.model.opt.timestep == 0.001


def test_control_moves_both_joints() -> None:
    runtime = load_arm()
    start = runtime.data.qpos.copy()
    for _ in range(100):
        step_control(runtime, np.array([0.55, -0.20]))
    assert runtime.data.qpos[0] > start[0] + 0.05
    assert runtime.data.qpos[1] > start[1] + 0.05
    assert all(np.isfinite(runtime.data.qpos))
    assert mujoco.mj_name2id(runtime.model, mujoco.mjtObj.mjOBJ_CAMERA, "overview") >= 0
