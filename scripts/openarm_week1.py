#!/usr/bin/env python3
"""Standalone OpenArm v1 W1 simulation diagnostic.

This script is intentionally limited to model loading, position-actuator
response, camera observations, and video evidence. It is not a task,
dataset collection, training, policy evaluation, or hardware entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import guard


OPENARM_COMMIT = "a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c"
OPENARM_SOURCE = "https://github.com/enactic/openarm_mujoco"
SCENE_RELATIVE_PATH = Path("v1/scene.xml")
BIMANUAL_RELATIVE_PATH = Path("v1/openarm_bimanual.xml")
SCENE_SHA256 = "c0263cda0c391162bce4b45ed1a4154b453603bb2a21427f0dc7bce9cb39edf0"
BIMANUAL_SHA256 = (
    "dad843e830e166048b478e7110ae4370e7d048487a1b725637c3695bde0e8c2a"
)
HOME_KEYFRAME = "home"
FPS = 20
MIN_DURATION_SECONDS = 30.0
MAX_DURATION_SECONDS = 120.0
OBSERVATION_SIZE = 224
EXPECTED_ACTUATOR_JOINTS = (
    ("left_joint1_ctrl", "openarm_left_joint1"),
    ("left_joint2_ctrl", "openarm_left_joint2"),
    ("left_joint3_ctrl", "openarm_left_joint3"),
    ("left_joint4_ctrl", "openarm_left_joint4"),
    ("left_joint5_ctrl", "openarm_left_joint5"),
    ("left_joint6_ctrl", "openarm_left_joint6"),
    ("left_joint7_ctrl", "openarm_left_joint7"),
    ("left_finger1_ctrl", "openarm_left_finger_joint1"),
    ("right_joint1_ctrl", "openarm_right_joint1"),
    ("right_joint2_ctrl", "openarm_right_joint2"),
    ("right_joint3_ctrl", "openarm_right_joint3"),
    ("right_joint4_ctrl", "openarm_right_joint4"),
    ("right_joint5_ctrl", "openarm_right_joint5"),
    ("right_joint6_ctrl", "openarm_right_joint6"),
    ("right_joint7_ctrl", "openarm_right_joint7"),
    ("right_finger1_ctrl", "openarm_right_finger_joint1"),
)
MOTION_SPECS = {
    "left_joint": {
        "actuator_index": 2,
        "amplitude": 0.20,
        "minimum_response": 0.03,
        "unit": "rad",
    },
    "right_joint": {
        "actuator_index": 10,
        "amplitude": -0.20,
        "minimum_response": 0.03,
        "unit": "rad",
    },
    "left_gripper": {
        "actuator_index": 7,
        "amplitude": -0.024,
        "minimum_response": 0.005,
        "unit": "m",
    },
    "right_gripper": {
        "actuator_index": 15,
        "amplitude": -0.024,
        "minimum_response": 0.005,
        "unit": "m",
    },
}
FORBIDDEN_ARGUMENT_TERMS = ("hardware", "real-robot", "ros", "sdk", "device")
OBJECTIVE_NAMES = (
    "source_pin_and_hash",
    "nu16_transmission_mapping",
    "home_reset_once",
    "zero_mujoco_warnings",
    "camera_observation_and_visibility",
    "left_joint_response",
    "right_joint_response",
    "left_gripper_response",
    "right_gripper_response",
    "commands_inside_original_ranges",
    "controlled_joint_positions_inside_original_ranges",
    "finite_state_control_and_force",
    "decoded_video_600_frames_and_30_seconds",
    "scope_is_not_learned_policy_or_task",
)


class ValidationError(RuntimeError):
    """Raised when a W1 acceptance condition cannot be satisfied."""


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    upstream_root: Path
    scene_path: Path
    storage_root: Path
    storage_identity: dict[str, Any]
    output_dir: Path


def smooth_bump(phase: float) -> float:
    """Return a C1-smooth unit bump for a normalized phase."""
    if phase <= 0.0 or phase >= 1.0:
        return 0.0
    return math.sin(math.pi * phase) ** 2


def command_for_time(
    home_ctrl: Sequence[float], time_seconds: float, cycle_seconds: float = 10.0
) -> list[float]:
    """Build a bounded diagnostic command with non-overlapping responses."""
    if len(home_ctrl) != len(EXPECTED_ACTUATOR_JOINTS):
        raise ValueError("home_ctrl must contain exactly 16 actuator values")
    if time_seconds < 0.0:
        raise ValueError("time_seconds must be non-negative")
    if cycle_seconds <= 0.0:
        raise ValueError("cycle_seconds must be positive")

    command = [float(value) for value in home_ctrl]
    cycle_time = time_seconds % cycle_seconds
    phase_width = cycle_seconds / 5.0
    phases = (
        ("left_joint", phase_width, 2.0 * phase_width),
        ("right_joint", 2.0 * phase_width, 3.0 * phase_width),
        ("left_gripper", 3.0 * phase_width, 4.0 * phase_width),
        ("right_gripper", 4.0 * phase_width, 5.0 * phase_width),
    )
    for name, start, end in phases:
        if start <= cycle_time < end:
            spec = MOTION_SPECS[name]
            normalized = (cycle_time - start) / (end - start)
            command[spec["actuator_index"]] += (
                spec["amplitude"] * smooth_bump(normalized)
            )
            break
    return command


def clamp_command(
    command: Sequence[float], control_ranges: Sequence[Sequence[float]]
) -> tuple[list[float], list[int]]:
    """Clamp a command to model actuator ranges and return changed indices."""
    if len(command) != len(control_ranges):
        raise ValueError("command and control_ranges lengths differ")
    clamped: list[float] = []
    changed: list[int] = []
    for index, (value, limits) in enumerate(zip(command, control_ranges)):
        if len(limits) != 2:
            raise ValueError("each control range must contain lower and upper")
        lower, upper = float(limits[0]), float(limits[1])
        if lower > upper:
            raise ValueError("invalid control range")
        bounded = min(max(float(value), lower), upper)
        clamped.append(bounded)
        if not math.isclose(bounded, float(value), rel_tol=0.0, abs_tol=1e-12):
            changed.append(index)
    return clamped, changed


def validate_duration(duration: float) -> float:
    duration = float(duration)
    if not math.isfinite(duration):
        raise ValueError("duration must be finite")
    if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        raise ValueError(
            f"duration must be within "
            f"{MIN_DURATION_SECONDS:.0f}..{MAX_DURATION_SECONDS:.0f} seconds"
        )
    return duration


def validate_camera_fovy(value: str | float) -> float:
    fovy = float(value)
    if not math.isfinite(fovy) or not 20.0 <= fovy <= 90.0:
        raise ValueError("camera fovy must be finite and within 20..90 degrees")
    return fovy


def expected_frame_count(duration: float, fps: int = FPS) -> int:
    validate_duration(duration)
    if fps != FPS:
        raise ValueError(f"fps must be exactly {FPS}")
    return math.ceil(duration * fps)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate OpenArm v1 bimanual model loading, actuator response, "
            "camera observation, and a 30-120 second video. Simulation only."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--duration",
        type=validate_duration,
        default=30.0,
        help="simulated video duration in seconds (30..120; default: 30)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        choices=(FPS,),
        default=FPS,
        help="fixed evidence-video rate (must be 20)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=640,
        help="video width, at least 224 (default: 640)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=480,
        help="video height, at least 224 (default: 480)",
    )
    parser.add_argument(
        "--camera",
        choices=("auto", "front_camera", "side_camera"),
        default="auto",
        help=(
            "official v1 camera; auto selects the best visibility "
            "(default: auto)"
        ),
    )
    parser.add_argument(
        "--camera-fovy",
        type=validate_camera_fovy,
        default=35.0,
        help="runtime vertical FOV for official cameras, 20..90 degrees (default: 35)",
    )
    return parser


def reject_hardware_arguments(argv: Sequence[str]) -> None:
    lowered = [argument.lower() for argument in argv]
    for argument in lowered:
        if any(term in argument for term in FORBIDDEN_ARGUMENT_TERMS):
            raise ValidationError(
                f"hardware/ROS argument is forbidden in W1 simulation: {argument}"
            )


def _git_output(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def verify_source(upstream_root: Path) -> dict[str, Any]:
    if not upstream_root.is_dir():
        raise ValidationError(
            f"missing pinned OpenArm checkout: {upstream_root}"
        )
    try:
        commit = _git_output(upstream_root, "rev-parse", "HEAD")
        dirty_v1 = _git_output(
            upstream_root, "status", "--porcelain", "--untracked-files=all", "--", "v1"
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValidationError(f"cannot verify OpenArm git checkout: {exc}") from exc
    if commit != OPENARM_COMMIT:
        raise ValidationError(
            f"OpenArm commit mismatch: expected {OPENARM_COMMIT}, got {commit}"
        )
    if dirty_v1:
        raise ValidationError(
            "OpenArm v1 model is modified or has untracked files; refusing to run"
        )

    scene_path = upstream_root / SCENE_RELATIVE_PATH
    bimanual_path = upstream_root / BIMANUAL_RELATIVE_PATH
    for path in (scene_path, bimanual_path):
        if not path.is_file():
            raise ValidationError(f"missing model file: {path}")
    scene_hash = sha256_file(scene_path)
    bimanual_hash = sha256_file(bimanual_path)
    if scene_hash != SCENE_SHA256:
        raise ValidationError(
            f"scene.xml hash mismatch: expected {SCENE_SHA256}, got {scene_hash}"
        )
    if bimanual_hash != BIMANUAL_SHA256:
        raise ValidationError(
            "openarm_bimanual.xml hash mismatch: "
            f"expected {BIMANUAL_SHA256}, got {bimanual_hash}"
        )
    return {
        "repository": OPENARM_SOURCE,
        "commit": commit,
        "checkout": str(upstream_root),
        "scene": str(SCENE_RELATIVE_PATH),
        "scene_sha256": scene_hash,
        "included_model": str(BIMANUAL_RELATIVE_PATH),
        "included_model_sha256": bimanual_hash,
        "v1_checkout_clean": True,
    }


def prepare_paths() -> RuntimePaths:
    project_root = Path(__file__).resolve().parents[1]
    storage_value = os.environ.get("DP_STORAGE_ROOT", "").strip()
    if not storage_value:
        raise ValidationError(
            "DP_STORAGE_ROOT is required; no local output fallback is allowed"
        )
    storage_root = Path(storage_value).expanduser()
    if not storage_root.is_absolute():
        raise ValidationError("DP_STORAGE_ROOT must be an absolute path")
    try:
        config = guard.load_config()
        expected = config["external"]
        configured_root = Path(expected["root"])
        if storage_root != configured_root:
            raise ValidationError(
                f"DP_STORAGE_ROOT is {storage_root}, expected exact guarded "
                f"root {configured_root}"
            )
        storage_identity = guard.query_mount(expected)
        guard.validate_storage_layout(
            Path(expected["mountpoint"]), configured_root
        )
    except (KeyError, TypeError, guard.GuardError) as exc:
        raise ValidationError(f"external storage guard failed: {exc}") from exc

    if not storage_root.exists() or not storage_root.is_dir():
        raise ValidationError(
            f"guarded DP_STORAGE_ROOT must already exist: {storage_root}"
        )
    if not os.access(storage_root, os.W_OK | os.X_OK):
        raise ValidationError(
            f"guarded DP_STORAGE_ROOT is not writable: {storage_root}"
        )

    outputs_root = storage_root / "outputs"
    try:
        guard.reject_symlink_components(outputs_root)
        guard.query_mount(expected)
    except guard.GuardError as exc:
        raise ValidationError(
            f"external storage changed before output creation: {exc}"
        ) from exc
    outputs_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        guard.reject_symlink_components(outputs_root)
    except guard.GuardError as exc:
        raise ValidationError(f"unsafe output path: {exc}") from exc
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%z")
    output_dir = outputs_root / f"openarm-week1-{timestamp}"
    try:
        guard.query_mount(expected)
        guard.reject_symlink_components(output_dir)
        output_dir.mkdir(mode=0o755, exist_ok=False)
    except FileExistsError as exc:
        raise ValidationError(f"refusing to overwrite output: {output_dir}") from exc
    except guard.GuardError as exc:
        raise ValidationError(
            f"external storage changed before run directory creation: {exc}"
        ) from exc
    try:
        guard.reject_symlink_components(output_dir)
        guard.query_mount(expected)
    except guard.GuardError as exc:
        raise ValidationError(
            f"external storage changed after run directory creation: {exc}"
        ) from exc

    upstream_root = project_root / "upstream" / "openarm_mujoco"
    return RuntimePaths(
        project_root=project_root,
        upstream_root=upstream_root,
        scene_path=upstream_root / SCENE_RELATIVE_PATH,
        storage_root=storage_root,
        storage_identity=storage_identity,
        output_dir=output_dir,
    )


def _enum_name(enum_type: Any, value: int) -> str:
    try:
        return enum_type(int(value)).name
    except (TypeError, ValueError):
        return str(int(value))


def inspect_model(mujoco: Any, model: Any) -> tuple[list[dict[str, Any]], list[int]]:
    if int(model.nu) != len(EXPECTED_ACTUATOR_JOINTS):
        raise ValidationError(f"expected nu=16, got nu={model.nu}")

    mappings: list[dict[str, Any]] = []
    qpos_addresses: list[int] = []
    joint_transmission = int(mujoco.mjtTrn.mjTRN_JOINT)
    for actuator_id, (expected_actuator, expected_joint) in enumerate(
        EXPECTED_ACTUATOR_JOINTS
    ):
        actuator_name = mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_ACTUATOR, actuator_id
        )
        transmission_type = int(model.actuator_trntype[actuator_id])
        joint_id = int(model.actuator_trnid[actuator_id, 0])
        transmission_aux_id = int(model.actuator_trnid[actuator_id, 1])
        joint_name = mujoco.mj_id2name(
            model, mujoco.mjtObj.mjOBJ_JOINT, joint_id
        )
        if actuator_name != expected_actuator or joint_name != expected_joint:
            raise ValidationError(
                "actuator transmission mapping mismatch at index "
                f"{actuator_id}: {(actuator_name, joint_name)}"
            )
        if transmission_type != joint_transmission:
            raise ValidationError(
                f"{actuator_name} is not a joint transmission"
            )
        joint_type = int(model.jnt_type[joint_id])
        joint_type_name = _enum_name(mujoco.mjtJoint, joint_type)
        if joint_type == int(mujoco.mjtJoint.mjJNT_HINGE):
            unit = "rad"
        elif joint_type == int(mujoco.mjtJoint.mjJNT_SLIDE):
            unit = "m"
        else:
            raise ValidationError(
                f"unsupported actuator joint type for {actuator_name}: "
                f"{joint_type_name}"
            )
        if not bool(model.actuator_ctrllimited[actuator_id]):
            raise ValidationError(f"{actuator_name} is not control-limited")
        qpos_address = int(model.jnt_qposadr[joint_id])
        qpos_addresses.append(qpos_address)
        mappings.append(
            {
                "order": actuator_id,
                "actuator_id": actuator_id,
                "actuator_name": actuator_name,
                "transmission_type": _enum_name(
                    mujoco.mjtTrn, transmission_type
                ),
                "transmission_ids": [joint_id, transmission_aux_id],
                "joint_id": joint_id,
                "joint_name": joint_name,
                "joint_type": joint_type_name,
                "qpos_address": qpos_address,
                "dof_address": int(model.jnt_dofadr[joint_id]),
                "control_range": [
                    float(model.actuator_ctrlrange[actuator_id, 0]),
                    float(model.actuator_ctrlrange[actuator_id, 1]),
                ],
                "joint_range": [
                    float(model.jnt_range[joint_id, 0]),
                    float(model.jnt_range[joint_id, 1]),
                ],
                "control_position_unit": unit,
                "observed_joint_position_unit": unit,
            }
        )
    return mappings, qpos_addresses


def apply_camera_fovy(
    mujoco: Any, model: Any, fovy: float
) -> dict[str, dict[str, Any]]:
    fovy = validate_camera_fovy(fovy)
    overrides = {}
    for name in ("front_camera", "side_camera"):
        camera_id = int(
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        )
        if camera_id < 0:
            raise ValidationError(f"camera not found: {name}")
        original_fovy = float(model.cam_fovy[camera_id])
        model.cam_fovy[camera_id] = fovy
        overrides[name] = {
            "original_vertical_fov_degrees": original_fovy,
            "actual_vertical_fov_degrees": float(model.cam_fovy[camera_id]),
            "override_reason": "W1 readability",
            "runtime_view_override_only": True,
            "camera_pose_unchanged": True,
        }
    return overrides


def camera_metadata(mujoco: Any, model: Any, camera_name: str) -> dict[str, Any]:
    camera_id = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera_name)
    )
    if camera_id < 0:
        raise ValidationError(f"camera not found: {camera_name}")
    return {
        "name": camera_name,
        "id": camera_id,
        "mode": _enum_name(mujoco.mjtCamLight, int(model.cam_mode[camera_id])),
        "position_m": [float(value) for value in model.cam_pos[camera_id]],
        "quaternion_wxyz": [
            float(value) for value in model.cam_quat[camera_id]
        ],
        "vertical_fov_degrees": float(model.cam_fovy[camera_id]),
    }


def warning_counters(mujoco: Any, data: Any) -> dict[str, int]:
    counters: dict[str, int] = {}
    for index, warning in enumerate(data.warning):
        counters[_enum_name(mujoco.mjtWarning, index)] = int(warning.number)
    return counters


def _assert_finite(np: Any, data: Any, frame_index: int, substep: int) -> None:
    arrays = {
        "qpos": data.qpos,
        "qvel": data.qvel,
        "qacc": data.qacc,
        "ctrl": data.ctrl,
        "actuator_force": data.actuator_force,
    }
    for name, values in arrays.items():
        if not bool(np.all(np.isfinite(values))):
            raise ValidationError(
                f"non-finite {name} at frame={frame_index}, substep={substep}"
            )


def advance_control_frame(
    mujoco: Any,
    model: Any,
    data: Any,
    substeps: int,
    after_dynamics: Any,
) -> None:
    """Advance dynamics, then refresh derived state without advancing time."""
    if substeps <= 0:
        raise ValueError("substeps must be positive")
    for substep in range(substeps):
        mujoco.mj_step(model, data)
        after_dynamics(substep)
    mujoco.mj_forward(model, data)
    after_dynamics(substeps)


def _observation_224(np: Any, frame: Any) -> Any:
    height, width, channels = frame.shape
    if channels != 3:
        raise ValidationError(f"RGB frame must have 3 channels, got {frame.shape}")
    side = min(height, width)
    y0 = (height - side) // 2
    x0 = (width - side) // 2
    square = frame[y0 : y0 + side, x0 : x0 + side]
    indices = np.linspace(0, side - 1, OBSERVATION_SIZE).round().astype(int)
    return square[indices[:, None], indices[None, :], :]


def _visible_groups(
    mujoco: Any, model: Any, segmentation: Any
) -> dict[str, int]:
    geom_type = int(mujoco.mjtObj.mjOBJ_GEOM)
    object_ids = segmentation[..., 0]
    object_types = segmentation[..., 1]
    groups = {
        "left_arm": 0,
        "right_arm": 0,
        "left_gripper": 0,
        "right_gripper": 0,
    }
    for geom_id in set(
        int(value) for value in object_ids[object_types == geom_type].tolist()
    ):
        if geom_id < 0:
            continue
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
        if not name:
            continue
        pixels = int(((object_ids == geom_id) & (object_types == geom_type)).sum())
        if name.startswith("openarm_left_") and "link" in name:
            groups["left_arm"] += pixels
        if name.startswith("openarm_right_") and "link" in name:
            groups["right_arm"] += pixels
        if name.startswith("openarm_left_") and (
            "finger" in name or "_hand_" in name
        ):
            groups["left_gripper"] += pixels
        if name.startswith("openarm_right_") and (
            "finger" in name or "_hand_" in name
        ):
            groups["right_gripper"] += pixels
    return groups


def _render_visibility(
    renderer: Any,
    mujoco: Any,
    model: Any,
    data: Any,
    camera_name: str,
) -> dict[str, int]:
    renderer.enable_segmentation_rendering()
    try:
        renderer.update_scene(data, camera=camera_name)
        segmentation = renderer.render().copy()
    finally:
        renderer.disable_segmentation_rendering()
    return _visible_groups(mujoco, model, segmentation)


def _select_camera(
    renderer: Any,
    mujoco: Any,
    model: Any,
    data: Any,
    requested_camera: str,
) -> tuple[str, dict[str, dict[str, int]]]:
    candidates = (
        ("front_camera", "side_camera")
        if requested_camera == "auto"
        else (requested_camera,)
    )
    candidate_visibility = {
        camera_name: _render_visibility(
            renderer, mujoco, model, data, camera_name
        )
        for camera_name in candidates
    }
    selected = max(
        candidates,
        key=lambda name: min(candidate_visibility[name].values()),
    )
    if any(
        pixel_count <= 0
        for pixel_count in candidate_visibility[selected].values()
    ):
        raise ValidationError(
            "no requested official camera shows both arms and both grippers: "
            f"{candidate_visibility}"
        )
    return selected, candidate_visibility


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def verify_decoded_video(
    imageio: Any,
    video_path: Path,
    expected_frames: int,
    expected_fps: int,
    minimum_duration: float,
) -> dict[str, Any]:
    reader = imageio.get_reader(video_path)
    decoded_frames = 0
    first_shape: list[int] | None = None
    metadata: dict[str, Any] = {}
    try:
        metadata = dict(reader.get_meta_data())
        for decoded in reader:
            if first_shape is None:
                first_shape = [int(value) for value in decoded.shape]
            decoded_frames += 1
    finally:
        reader.close()
    decoded_fps = float(metadata.get("fps", expected_fps))
    decoded_duration = decoded_frames / decoded_fps if decoded_fps > 0 else 0.0
    passed = (
        decoded_frames >= expected_frames
        and decoded_frames >= int(MIN_DURATION_SECONDS * FPS)
        and math.isclose(decoded_fps, expected_fps, rel_tol=0.0, abs_tol=0.05)
        and decoded_duration + (0.5 / expected_fps) >= minimum_duration
    )
    return {
        "method": "imageio decoded-frame iteration",
        "decoded_frame_count": decoded_frames,
        "expected_minimum_frame_count": expected_frames,
        "decoded_fps": decoded_fps,
        "decoded_duration_seconds": decoded_duration,
        "first_decoded_frame_shape": first_shape,
        "passed": passed,
    }


def _response_results(
    np: Any,
    requested: Any,
    observed: Any,
    home_ctrl: Any,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, spec in MOTION_SPECS.items():
        index = int(spec["actuator_index"])
        requested_delta = requested[:, index] - home_ctrl[index]
        observed_delta = observed[:, index] - home_ctrl[index]
        requested_peak_index = int(np.argmax(np.abs(requested_delta)))
        requested_excursion = float(np.max(np.abs(requested_delta)))
        observed_excursion = float(np.max(np.abs(observed_delta)))
        signed_response_at_requested_peak = float(
            observed_delta[requested_peak_index]
        )
        direction_passed = (
            signed_response_at_requested_peak * float(spec["amplitude"]) > 0.0
        )
        passed = (
            requested_excursion >= abs(float(spec["amplitude"])) * 0.95
            and observed_excursion >= float(spec["minimum_response"])
            and direction_passed
        )
        results[name] = {
            "actuator_index": index,
            "actuator_name": EXPECTED_ACTUATOR_JOINTS[index][0],
            "joint_name": EXPECTED_ACTUATOR_JOINTS[index][1],
            "unit": spec["unit"],
            "requested_excursion": requested_excursion,
            "observed_excursion": observed_excursion,
            "minimum_observed_excursion": float(spec["minimum_response"]),
            "signed_response_at_requested_peak": signed_response_at_requested_peak,
            "direction_passed": bool(direction_passed),
            "passed": bool(passed),
        }
    return results


def _trace_excerpt(
    times: Any, requested: Any, observed: Any
) -> dict[str, list[dict[str, float]]]:
    traces: dict[str, list[dict[str, float]]] = {}
    stride = max(1, len(times) // 120)
    sample_indices = list(range(0, len(times), stride))
    if sample_indices[-1] != len(times) - 1:
        sample_indices.append(len(times) - 1)
    for name, spec in MOTION_SPECS.items():
        index = int(spec["actuator_index"])
        traces[name] = [
            {
                "time_seconds": float(times[sample]),
                "requested": float(requested[sample, index]),
                "observed": float(observed[sample, index]),
            }
            for sample in sample_indices
        ]
    return traces


def _save_trace_archive(
    np: Any,
    report: dict[str, Any],
    trace_path: Path,
    completed_frames: int,
    sample_rate_hz: int,
    times: Any,
    qpos_trace: Any,
    qvel_trace: Any,
    requested_trace: Any,
    actual_trace: Any,
    *,
    complete: bool,
) -> None:
    np.savez_compressed(
        trace_path,
        time_seconds=times[:completed_frames],
        qpos=qpos_trace[:completed_frames],
        qvel=qvel_trace[:completed_frames],
        requested_ctrl=requested_trace[:completed_frames],
        observed_actuator_joint_qpos=actual_trace[:completed_frames],
    )
    report["trace"] = {
        "path": trace_path.name,
        "format": "numpy savez compressed",
        "complete": complete,
        "completed_frames": completed_frames,
        "arrays": {
            "time_seconds": list(times[:completed_frames].shape),
            "qpos": list(qpos_trace[:completed_frames].shape),
            "qvel": list(qvel_trace[:completed_frames].shape),
            "requested_ctrl": list(requested_trace[:completed_frames].shape),
            "observed_actuator_joint_qpos": list(
                actual_trace[:completed_frames].shape
            ),
        },
        "file_size_bytes": trace_path.stat().st_size,
        "sample_rate_hz": sample_rate_hz,
    }


def run_validation(args: argparse.Namespace, paths: RuntimePaths) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "running",
        "passed": False,
        "scope": {
            "classification": "OpenArm W1 basic simulation diagnostic",
            "learned_policy": False,
            "task_training": False,
            "dataset_collection": False,
            "task_object_or_handover": False,
            "hardware_ros_or_real_sdk": False,
            "future_w2_model_choice_frozen": False,
        },
        "storage_guard": {
            "configured_root": str(paths.storage_root),
            "mount_identity": paths.storage_identity,
        },
        "output_directory": str(paths.output_dir),
        "objectives": {name: False for name in OBJECTIVE_NAMES},
    }
    report_path = paths.output_dir / "report.json"
    renderer = None
    writer = None
    last_frame = None
    caught_error: BaseException | None = None
    np_module = None
    trace_path = paths.output_dir / "trace.npz"
    times = None
    qpos_trace = None
    qvel_trace = None
    requested_trace = None
    actual_trace = None
    completed_frames = 0
    try:
        # Imports are delayed so pure tests do not require the simulation stack.
        import imageio.v2 as imageio
        import mujoco
        import numpy as np

        np_module = np
        report["runtime"] = {
            "python": sys.version.split()[0],
            "mujoco": getattr(mujoco, "__version__", "unknown"),
            "numpy": getattr(np, "__version__", "unknown"),
            "imageio": getattr(imageio, "__version__", "unknown"),
        }
        report["source"] = verify_source(paths.upstream_root)
        report["objectives"]["source_pin_and_hash"] = True

        model = mujoco.MjModel.from_xml_path(str(paths.scene_path))
        data = mujoco.MjData(model)
        mappings, qpos_addresses = inspect_model(mujoco, model)
        names_buffer = bytes(model.names)
        report["model"] = {
            "name": names_buffer.split(b"\x00", 1)[0].decode(
                "utf-8", errors="replace"
            ),
            "nq": int(model.nq),
            "nv": int(model.nv),
            "nu": int(model.nu),
            "na": int(model.na),
            "timestep_seconds": float(model.opt.timestep),
            "integrator": _enum_name(mujoco.mjtIntegrator, int(model.opt.integrator)),
            "actuator_joint_mappings": mappings,
        }
        report["objectives"]["nu16_transmission_mapping"] = True

        keyframe_id = int(
            mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_KEY, HOME_KEYFRAME
            )
        )
        if keyframe_id < 0:
            raise ValidationError(f"missing keyframe: {HOME_KEYFRAME}")
        mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
        mujoco.mj_forward(model, data)
        home_ctrl = np.asarray(data.ctrl, dtype=np.float64).copy()
        home_qpos = np.asarray(data.qpos, dtype=np.float64).copy()
        if home_ctrl.shape != (16,):
            raise ValidationError(
                f"home control shape must be (16,), got {home_ctrl.shape}"
            )
        control_ranges = np.asarray(model.actuator_ctrlrange, dtype=np.float64)
        if not bool(
            np.all(home_ctrl >= control_ranges[:, 0])
            and np.all(home_ctrl <= control_ranges[:, 1])
        ):
            raise ValidationError("home controls are outside actuator ranges")
        report["home_reset"] = {
            "keyframe": HOME_KEYFRAME,
            "keyframe_id": keyframe_id,
            "reset_count": 1,
            "forward_after_reset_before_camera_selection": True,
            "home_ctrl": home_ctrl.tolist(),
            "home_qpos": home_qpos.tolist(),
        }
        report["objectives"]["home_reset_once"] = True

        dt = float(model.opt.timestep)
        video_period = 1.0 / args.fps
        substeps_float = video_period / dt
        substeps = int(round(substeps_float))
        if substeps <= 0 or not math.isclose(
            substeps * dt, video_period, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValidationError(
                f"model dt={dt} cannot produce exactly {args.fps} FPS"
            )
        frame_count = expected_frame_count(args.duration, args.fps)
        report["simulation"] = {
            "requested_duration_seconds": float(args.duration),
            "fps": int(args.fps),
            "expected_frame_count": frame_count,
            "physics_timestep_seconds": dt,
            "substeps_per_frame": substeps,
            "control_update_rate_hz": int(args.fps),
            "control_method": "position actuators followed by mujoco.mj_step",
            "post_step_forward_before_record_and_render": True,
            "qpos_injection_after_home_reset": False,
            "renderer_threading": "single main thread",
        }

        if args.width < OBSERVATION_SIZE or args.height < OBSERVATION_SIZE:
            raise ValidationError(
                f"video dimensions must be at least {OBSERVATION_SIZE}x"
                f"{OBSERVATION_SIZE}"
            )
        if args.width % 2 or args.height % 2:
            raise ValidationError("video width and height must be even for H.264")
        fovy_overrides = apply_camera_fovy(mujoco, model, args.camera_fovy)
        report["camera_fovy_overrides"] = fovy_overrides
        renderer = mujoco.Renderer(
            model, height=int(args.height), width=int(args.width)
        )
        selected_camera, camera_candidates = _select_camera(
            renderer, mujoco, model, data, args.camera
        )
        camera = camera_metadata(mujoco, model, selected_camera)
        camera.update(fovy_overrides[selected_camera])
        camera.update(
            {
                "requested": args.camera,
                "selection_method": (
                    "maximum minimum segmentation pixels across left/right "
                    "arms and grippers at home"
                ),
                "home_visibility_candidates": camera_candidates,
                "video_rgb_shape": [args.height, args.width, 3],
                "observation_rgb_shape": [
                    OBSERVATION_SIZE,
                    OBSERVATION_SIZE,
                    3,
                ],
                "observation_transform": (
                    "center square crop followed by nearest-neighbor resize"
                ),
                "observation_dtype": "uint8",
            }
        )
        report["camera"] = camera

        video_path = paths.output_dir / "openarm-week1.mp4"
        writer = imageio.get_writer(
            video_path,
            fps=args.fps,
            codec="libx264",
            quality=8,
            macro_block_size=None,
            pixelformat="yuv420p",
        )

        times = np.empty(frame_count, dtype=np.float64)
        qpos_trace = np.empty((frame_count, int(model.nq)), dtype=np.float32)
        qvel_trace = np.empty((frame_count, int(model.nv)), dtype=np.float32)
        requested_trace = np.empty((frame_count, int(model.nu)), dtype=np.float32)
        actual_trace = np.empty((frame_count, int(model.nu)), dtype=np.float32)
        checkpoint_frames = {
            0: "first",
            frame_count // 2: "mid",
            frame_count - 1: "last",
        }
        visibility: dict[str, dict[str, int]] = {}
        clamp_events: list[dict[str, Any]] = []
        first_observation = None
        maximum_observation_change = 0.0
        observation_std_min = math.inf
        observation_std_max = 0.0

        for frame_index in range(frame_count):
            requested_unclamped = command_for_time(
                home_ctrl, frame_index / args.fps
            )
            requested, changed = clamp_command(
                requested_unclamped, control_ranges
            )
            if changed:
                clamp_events.append(
                    {
                        "frame_index": frame_index,
                        "actuator_indices": changed,
                    }
                )
            data.ctrl[:] = requested
            advance_control_frame(
                mujoco,
                model,
                data,
                substeps,
                lambda substep: _assert_finite(
                    np, data, frame_index, substep
                ),
            )
            times[frame_index] = float(data.time)
            qpos_trace[frame_index] = data.qpos
            qvel_trace[frame_index] = data.qvel
            requested_trace[frame_index] = data.ctrl
            actual_trace[frame_index] = data.qpos[qpos_addresses]
            completed_frames = frame_index + 1

            renderer.update_scene(data, camera=selected_camera)
            frame = renderer.render().copy()
            if frame.shape != (args.height, args.width, 3):
                raise ValidationError(
                    f"unexpected camera frame shape: {frame.shape}"
                )
            if frame.dtype != np.uint8:
                raise ValidationError(
                    f"unexpected camera frame dtype: {frame.dtype}"
                )
            observation = _observation_224(np, frame)
            if observation.shape != (
                OBSERVATION_SIZE,
                OBSERVATION_SIZE,
                3,
            ):
                raise ValidationError(
                    f"unexpected observation shape: {observation.shape}"
                )
            observation_std = float(observation.std())
            observation_std_min = min(observation_std_min, observation_std)
            observation_std_max = max(observation_std_max, observation_std)
            if first_observation is None:
                first_observation = observation.astype(np.int16)
            else:
                change = float(
                    np.mean(
                        np.abs(
                            observation.astype(np.int16) - first_observation
                        )
                    )
                )
                maximum_observation_change = max(
                    maximum_observation_change, change
                )

            writer.append_data(frame)
            if frame_index in checkpoint_frames:
                label = checkpoint_frames[frame_index]
                imageio.imwrite(paths.output_dir / f"{label}.png", frame)
                visibility[label] = _render_visibility(
                    renderer, mujoco, model, data, selected_camera
                )
            last_frame = frame

        if last_frame is None:
            raise ValidationError("no video frames were rendered")
        writer.close()
        writer = None
        renderer.close()
        renderer = None

        counters = warning_counters(mujoco, data)
        report["warnings"] = {
            "counters": counters,
            "total": sum(counters.values()),
        }
        warnings_passed = all(count == 0 for count in counters.values())
        report["objectives"]["zero_mujoco_warnings"] = warnings_passed

        camera_passed = (
            observation_std_min > 1.0
            and observation_std_max > 1.0
            and maximum_observation_change > 0.05
            and all(
                all(pixel_count > 0 for pixel_count in groups.values())
                for groups in visibility.values()
            )
        )
        report["camera"]["visibility_pixels"] = visibility
        report["camera"]["observation_std_min"] = observation_std_min
        report["camera"]["observation_std_max"] = observation_std_max
        report["camera"][
            "maximum_mean_absolute_change_from_first"
        ] = maximum_observation_change
        report["objectives"]["camera_observation_and_visibility"] = camera_passed

        response = _response_results(
            np, requested_trace, actual_trace, home_ctrl
        )
        report["response"] = {
            "results": response,
            "trace_excerpt_20hz_decimated_to_at_most_121_samples": (
                _trace_excerpt(times, requested_trace, actual_trace)
            ),
        }
        for name, result in response.items():
            report["objectives"][f"{name}_response"] = bool(result["passed"])

        _save_trace_archive(
            np,
            report,
            trace_path,
            completed_frames,
            args.fps,
            times,
            qpos_trace,
            qvel_trace,
            requested_trace,
            actual_trace,
            complete=True,
        )
        report["control_limits"] = {
            "all_commands_within_original_actuator_ranges": not clamp_events,
            "clamp_events": clamp_events,
        }
        report["objectives"]["commands_inside_original_ranges"] = (
            not clamp_events
        )
        controlled_ranges = np.asarray(
            [mapping["joint_range"] for mapping in mappings], dtype=np.float64
        )
        observed_min = np.min(actual_trace, axis=0)
        observed_max = np.max(actual_trace, axis=0)
        joint_range_tolerance = 1e-5
        joint_positions_inside_ranges = bool(
            np.all(observed_min >= controlled_ranges[:, 0] - joint_range_tolerance)
            and np.all(
                observed_max <= controlled_ranges[:, 1] + joint_range_tolerance
            )
        )
        report["observed_controlled_joint_ranges"] = [
            {
                "actuator_index": index,
                "joint_name": mappings[index]["joint_name"],
                "unit": mappings[index]["observed_joint_position_unit"],
                "original_joint_range": mappings[index]["joint_range"],
                "observed_minimum": float(observed_min[index]),
                "observed_maximum": float(observed_max[index]),
                "lower_limit_violation": max(
                    0.0,
                    float(controlled_ranges[index, 0] - observed_min[index]),
                ),
                "upper_limit_violation": max(
                    0.0,
                    float(observed_max[index] - controlled_ranges[index, 1]),
                ),
                "acceptance_tolerance": joint_range_tolerance,
            }
            for index in range(len(mappings))
        ]
        report["objectives"][
            "controlled_joint_positions_inside_original_ranges"
        ] = joint_positions_inside_ranges
        report["objectives"]["finite_state_control_and_force"] = True

        video_verification = verify_decoded_video(
            imageio,
            video_path,
            frame_count,
            args.fps,
            args.duration,
        )
        report["video"] = {
            "path": video_path.name,
            "container": "MP4",
            "codec_requested": "libx264",
            "file_size_bytes": video_path.stat().st_size,
            "raw_frames_retained_in_memory": False,
            "saved_still_frames": ["first.png", "mid.png", "last.png"],
            "decoded_verification": video_verification,
        }
        report["objectives"]["decoded_video_600_frames_and_30_seconds"] = bool(
            video_verification["passed"]
        )

        report["simulation"]["actual_simulated_seconds"] = float(data.time)
        report["simulation"]["recorded_frame_count"] = frame_count
        report["objectives"]["scope_is_not_learned_policy_or_task"] = True
        report["passed"] = all(bool(value) for value in report["objectives"].values())
        if not report["passed"]:
            failed = [
                name
                for name, passed in report["objectives"].items()
                if not passed
            ]
            raise ValidationError(
                "W1 validation objectives failed: " + ", ".join(failed)
            )
        report["status"] = "passed"
        return report
    except BaseException as exc:
        caught_error = exc
        report["status"] = "failed"
        report["passed"] = False
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        raise
    finally:
        if (
            np_module is not None
            and completed_frames > 0
            and not trace_path.exists()
            and times is not None
            and qpos_trace is not None
            and qvel_trace is not None
            and requested_trace is not None
            and actual_trace is not None
        ):
            try:
                _save_trace_archive(
                    np_module,
                    report,
                    trace_path,
                    completed_frames,
                    args.fps,
                    times,
                    qpos_trace,
                    qvel_trace,
                    requested_trace,
                    actual_trace,
                    complete=False,
                )
            except BaseException as cleanup_error:
                report.setdefault("cleanup_errors", []).append(
                    f"partial trace: {cleanup_error}"
                )
        if writer is not None:
            try:
                writer.close()
            except BaseException as cleanup_error:
                report.setdefault("cleanup_errors", []).append(
                    f"video writer: {cleanup_error}"
                )
        if renderer is not None:
            try:
                renderer.close()
            except BaseException as cleanup_error:
                report.setdefault("cleanup_errors", []).append(
                    f"renderer: {cleanup_error}"
                )
        report["completed_at"] = datetime.now().astimezone().isoformat()
        if caught_error is None and report.get("status") == "running":
            report["status"] = "failed"
            report["passed"] = False
        _write_json_atomic(report_path, report)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    paths: RuntimePaths | None = None
    try:
        reject_hardware_arguments(arguments)
        args = build_parser().parse_args(arguments)
        if args.width < OBSERVATION_SIZE or args.height < OBSERVATION_SIZE:
            raise ValidationError(
                f"width and height must each be at least {OBSERVATION_SIZE}"
            )
        paths = prepare_paths()
        report = run_validation(args, paths)
    except (ValidationError, ValueError) as exc:
        report_hint = (
            f"; report: {paths.output_dir / 'report.json'}" if paths else ""
        )
        print(f"openarm_week1: {exc}{report_hint}", file=sys.stderr)
        return 1
    except ImportError as exc:
        report_hint = (
            f"; report: {paths.output_dir / 'report.json'}" if paths else ""
        )
        print(
            "openarm_week1: missing runtime dependency; install only through "
            f"scripts/run-safe.sh after parent approval: {exc}{report_hint}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        report_hint = (
            f"; report: {paths.output_dir / 'report.json'}" if paths else ""
        )
        print(
            f"openarm_week1: unexpected failure: {exc}{report_hint}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"passed": True, "report": str(paths.output_dir / "report.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
