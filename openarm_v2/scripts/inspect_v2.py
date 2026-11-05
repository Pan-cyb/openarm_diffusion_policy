#!/usr/bin/env python3
"""Inspect the official OpenArm V2 pedestal model without advancing physics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mujoco


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    PROJECT_ROOT
    / "vendor"
    / "openarm_mujoco"
    / "v2"
    / "pedestal"
    / "pedestal.xml"
)


def object_names(model: mujoco.MjModel, kind: mujoco.mjtObj, count: int) -> list[str]:
    return [
        mujoco.mj_id2name(model, kind, index) or f"<unnamed:{index}>"
        for index in range(count)
    ]


def inspect_model() -> dict[str, Any]:
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    home_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    if home_id < 0:
        raise RuntimeError("V2 pedestal model is missing the home keyframe")

    mujoco.mj_resetDataKeyframe(model, data, home_id)
    for actuator_id in range(model.nu):
        joint_id = int(model.actuator_trnid[actuator_id, 0])
        qpos_address = int(model.jnt_qposadr[joint_id])
        data.ctrl[actuator_id] = data.qpos[qpos_address]
    mujoco.mj_forward(model, data)

    return {
        "model_path": str(MODEL_PATH),
        "dimensions": {
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "nbody": int(model.nbody),
            "njnt": int(model.njnt),
            "ncam": int(model.ncam),
            "nkey": int(model.nkey),
        },
        "home_keyframe_id": int(home_id),
        "joints": object_names(model, mujoco.mjtObj.mjOBJ_JOINT, model.njnt),
        "actuators": object_names(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu
        ),
        "cameras": object_names(model, mujoco.mjtObj.mjOBJ_CAMERA, model.ncam),
        "home_qpos": [float(value) for value in data.qpos],
        "home_ctrl": [float(value) for value in data.ctrl],
    }


def main() -> None:
    print(json.dumps(inspect_model(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
