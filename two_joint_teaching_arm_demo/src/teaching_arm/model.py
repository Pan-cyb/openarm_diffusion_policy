from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = PROJECT_ROOT / "model" / "two_joint_arm.xml"
HOME_Q = np.array([0.25, -0.50], dtype=np.float64)
JOINT_LIMITS = np.array([[-1.20, 1.20], [-1.40, 1.40]], dtype=np.float64)
CONTROL_HZ = 100
PHYSICS_HZ = 1000
SUBSTEPS = PHYSICS_HZ // CONTROL_HZ


@dataclass
class ArmRuntime:
    model: mujoco.MjModel
    data: mujoco.MjData


def load_arm() -> ArmRuntime:
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if key_id < 0:
        raise RuntimeError("home keyframe is missing")
    mujoco.mj_resetDataKeyframe(model, data, key_id)
    mujoco.mj_forward(model, data)
    return ArmRuntime(model=model, data=data)


def step_control(runtime: ArmRuntime, target_q: np.ndarray) -> None:
    bounded = np.clip(np.asarray(target_q, dtype=np.float64), JOINT_LIMITS[:, 0], JOINT_LIMITS[:, 1])
    runtime.data.ctrl[:] = bounded
    for _ in range(SUBSTEPS):
        mujoco.mj_step(runtime.model, runtime.data)


def reset_home(runtime: ArmRuntime) -> None:
    key_id = mujoco.mj_name2id(runtime.model, mujoco.mjtObj.mjOBJ_KEY, "home")
    mujoco.mj_resetDataKeyframe(runtime.model, runtime.data, key_id)
    mujoco.mj_forward(runtime.model, runtime.data)
