from pathlib import Path

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


def names(model: mujoco.MjModel, kind: mujoco.mjtObj, count: int) -> list[str]:
    return [
        mujoco.mj_id2name(model, kind, index) or ""
        for index in range(count)
    ]


def test_v2_pedestal_baseline() -> None:
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))

    assert (model.nq, model.nv, model.nu) == (18, 18, 16)
    assert model.ncam == 2
    assert model.nkey == 1
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home") == 0


def test_v2_actuator_and_camera_names() -> None:
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    actuators = names(model, mujoco.mjtObj.mjOBJ_ACTUATOR, model.nu)
    cameras = names(model, mujoco.mjtObj.mjOBJ_CAMERA, model.ncam)

    assert actuators[0] == "left_joint1_ctrl"
    assert actuators[7] == "left_finger1_ctrl"
    assert actuators[8] == "right_joint1_ctrl"
    assert actuators[15] == "right_finger1_ctrl"
    assert cameras == ["camera_wrist_left", "camera_wrist_right"]


def test_mainline_contains_no_v1_model_directory() -> None:
    assert not any(
        path.is_dir() and path.name == "v1"
        for path in PROJECT_ROOT.rglob("v1")
    )
