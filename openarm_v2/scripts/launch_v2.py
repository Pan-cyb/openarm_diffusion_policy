#!/usr/bin/env python3
"""Launch the official OpenArm V2 pedestal model in MuJoCo."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import mujoco
import mujoco.viewer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = (
    PROJECT_ROOT
    / "vendor"
    / "openarm_mujoco"
    / "v2"
    / "pedestal"
    / "pedestal.xml"
)


def load_home() -> tuple[mujoco.MjModel, mujoco.MjData]:
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
    return model, data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the official OpenArm V2 pedestal scene."
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="display the home pose without advancing physics",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    model, data = load_home()

    if not args.static:
        mujoco.viewer.launch(model, data)
        return

    with mujoco.viewer.launch_passive(model, data) as viewer:
        while viewer.is_running():
            frame_start = time.perf_counter()
            mujoco.mj_forward(model, data)
            viewer.sync()
            remaining = 1.0 / 60.0 - (time.perf_counter() - frame_start)
            if remaining > 0:
                time.sleep(remaining)


if __name__ == "__main__":
    main()
