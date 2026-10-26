from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "openarm_week1.py"
SPEC = importlib.util.spec_from_file_location("openarm_week1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
openarm_week1 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = openarm_week1
SPEC.loader.exec_module(openarm_week1)


HOME = [
    0.0,
    0.0,
    0.0,
    1.570796,
    0.0,
    0.0,
    0.0,
    0.044,
    0.0,
    0.0,
    0.0,
    1.570796,
    0.0,
    0.0,
    0.0,
    0.044,
]


def test_cli_duration_text_is_converted_before_validation() -> None:
    args = openarm_week1.build_parser().parse_args(["--duration", "30"])
    assert args.duration == 30.0


def test_camera_fovy_default() -> None:
    assert openarm_week1.build_parser().parse_args([]).camera_fovy == 35.0


@pytest.mark.parametrize("value", ["20", "35", "40.5", "90"])
def test_camera_fovy_cli_accepts_valid_degrees(value: str) -> None:
    args = openarm_week1.build_parser().parse_args(["--camera-fovy", value])
    assert args.camera_fovy == float(value)


@pytest.mark.parametrize("value", ["19.9", "90.1", "nan", "inf", "-inf", "bad"])
def test_camera_fovy_cli_rejects_invalid_degrees(value: str) -> None:
    with pytest.raises(SystemExit):
        openarm_week1.build_parser().parse_args([f"--camera-fovy={value}"])


def test_camera_fovy_override_is_view_only_and_records_original() -> None:
    mujoco = SimpleNamespace(
        mjtObj=SimpleNamespace(mjOBJ_CAMERA=1),
        mj_name2id=lambda model, kind, name: {
            "front_camera": 0, "side_camera": 1
        }[name],
    )
    model = SimpleNamespace(
        cam_fovy=[90.0, 90.0, 60.0],
        cam_pos=[(0.0, 2.0, 0.5), (-2.0, 0.0, 0.5)],
        cam_quat=[(0.0, 0.0, 0.707, 0.707), (-0.5, -0.5, 0.5, 0.5)],
        opt=SimpleNamespace(timestep=0.001),
    )
    before = dict(model.__dict__)
    metadata = openarm_week1.apply_camera_fovy(mujoco, model, 35.0)
    assert model.cam_fovy == [35.0, 35.0, 60.0]
    assert {key: value for key, value in model.__dict__.items() if key != "cam_fovy"} == {
        key: value for key, value in before.items() if key != "cam_fovy"
    }
    for name in ("front_camera", "side_camera"):
        assert metadata[name]["original_vertical_fov_degrees"] == 90.0
        assert metadata[name]["actual_vertical_fov_degrees"] == 35.0
        assert metadata[name]["override_reason"] == "W1 readability"
        assert metadata[name]["camera_pose_unchanged"] is True
        assert metadata[name]["runtime_view_override_only"] is True


def test_smooth_bump_has_zero_endpoints_and_unit_peak() -> None:
    assert openarm_week1.smooth_bump(-1.0) == 0.0
    assert openarm_week1.smooth_bump(0.0) == 0.0
    assert openarm_week1.smooth_bump(1.0) == 0.0
    assert openarm_week1.smooth_bump(2.0) == 0.0
    assert math.isclose(openarm_week1.smooth_bump(0.5), 1.0)


@pytest.mark.parametrize(
    ("time_seconds", "active_index", "expected_delta"),
    [
        (1.0, None, 0.0),
        (3.0, 2, 0.20),
        (5.0, 10, -0.20),
        (7.0, 7, -0.024),
        (9.0, 15, -0.024),
        (13.0, 2, 0.20),
    ],
)
def test_command_phases_are_separate_and_repeat(
    time_seconds: float, active_index: int | None, expected_delta: float
) -> None:
    command = openarm_week1.command_for_time(HOME, time_seconds)
    changed = [
        index
        for index, (actual, home) in enumerate(zip(command, HOME))
        if not math.isclose(actual, home, abs_tol=1e-12)
    ]
    if active_index is None:
        assert changed == []
    else:
        assert changed == [active_index]
        assert math.isclose(
            command[active_index] - HOME[active_index],
            expected_delta,
            abs_tol=1e-12,
        )


def test_command_is_continuous_at_phase_boundaries() -> None:
    epsilon = 1e-7
    for boundary in (2.0, 4.0, 6.0, 8.0, 10.0):
        before = openarm_week1.command_for_time(HOME, boundary - epsilon)
        at = openarm_week1.command_for_time(HOME, boundary)
        after = openarm_week1.command_for_time(HOME, boundary + epsilon)
        assert max(abs(a - b) for a, b in zip(before, at)) < 1e-10
        assert max(abs(a - b) for a, b in zip(after, at)) < 1e-10


def test_commands_stay_inside_official_v1_ranges() -> None:
    ranges = [
        (-3.490659, 1.396263),
        (-3.3161253267948965, 0.17453267320510335),
        (-1.570796, 1.570796),
        (0.0, 2.443461),
        (-1.570796, 1.570796),
        (-0.785398, 0.785398),
        (-1.570796, 1.570796),
        (-0.005, 0.044),
        (-1.396263, 3.490659),
        (-0.17453267320510335, 3.3161253267948965),
        (-1.570796, 1.570796),
        (0.0, 2.443461),
        (-1.570796, 1.570796),
        (-0.785398, 0.785398),
        (-1.570796, 1.570796),
        (-0.005, 0.044),
    ]
    for sample in range(1001):
        command = openarm_week1.command_for_time(HOME, sample / 100.0)
        clamped, changed = openarm_week1.clamp_command(command, ranges)
        assert changed == []
        assert clamped == command


def test_clamp_reports_out_of_range_indices() -> None:
    command = [0.0, 2.0, -2.0]
    clamped, changed = openarm_week1.clamp_command(
        command, [(-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0)]
    )
    assert clamped == [0.0, 1.0, -1.0]
    assert changed == [1, 2]


def test_control_frame_forwards_only_after_all_steps() -> None:
    events: list[str] = []

    class FakeMujoco:
        @staticmethod
        def mj_step(model: object, data: object) -> None:
            events.append("step")

        @staticmethod
        def mj_forward(model: object, data: object) -> None:
            events.append("forward")

    checks: list[int] = []
    openarm_week1.advance_control_frame(
        FakeMujoco,
        object(),
        object(),
        3,
        checks.append,
    )
    assert events == ["step", "step", "step", "forward"]
    assert checks == [0, 1, 2, 3]


@pytest.mark.parametrize("duration", [29.999, 120.001, math.inf, math.nan])
def test_duration_rejects_out_of_bounds(duration: float) -> None:
    with pytest.raises(ValueError):
        openarm_week1.validate_duration(duration)


def test_duration_and_frame_count_acceptance_bounds() -> None:
    assert openarm_week1.expected_frame_count(30.0) == 600
    assert openarm_week1.expected_frame_count(30.01) == 601
    assert openarm_week1.expected_frame_count(120.0) == 2400
    with pytest.raises(ValueError):
        openarm_week1.expected_frame_count(30.0, fps=10)


@pytest.mark.parametrize(
    "argument", ["--hardware", "--real-robot", "--ros-node", "--device=/dev/ttyUSB0"]
)
def test_hardware_arguments_are_rejected(argument: str) -> None:
    with pytest.raises(openarm_week1.ValidationError):
        openarm_week1.reject_hardware_arguments([argument])


def test_parser_has_no_hardware_entry_point() -> None:
    parser = openarm_week1.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--real"])


def _guard_config(mountpoint: Path, storage_root: Path) -> dict:
    return {
        "external": {
            "mountpoint": str(mountpoint),
            "root": str(storage_root),
            "uuid": "TEST-UUID",
            "fstype": "exfat",
            "disk_serial": "TEST-SERIAL",
        }
    }


def test_prepare_paths_uses_exact_guarded_storage_before_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountpoint = tmp_path / "mount"
    storage_root = mountpoint / "dp_week1"
    storage_root.mkdir(parents=True)
    config = _guard_config(mountpoint, storage_root)
    identity = {
        "source": "/dev/test2",
        "target": str(mountpoint),
        "fstype": "exfat",
        "uuid": "TEST-UUID",
        "parent_disk": {"serial": "TEST-SERIAL"},
    }
    calls: list[dict] = []

    monkeypatch.setenv("DP_STORAGE_ROOT", str(storage_root))
    monkeypatch.setattr(openarm_week1.guard, "load_config", lambda: config)

    def fake_query(expected: dict) -> dict:
        calls.append(expected)
        return identity

    monkeypatch.setattr(openarm_week1.guard, "query_mount", fake_query)
    paths = openarm_week1.prepare_paths()

    assert paths.storage_root == storage_root
    assert paths.storage_identity == identity
    assert paths.output_dir.parent == storage_root / "outputs"
    assert paths.output_dir.is_dir()
    assert len(calls) == 4
    assert all(expected is config["external"] for expected in calls)


def test_prepare_paths_rejects_nonexact_storage_without_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountpoint = tmp_path / "mount"
    configured_root = mountpoint / "dp_week1"
    wrong_root = mountpoint / "wrong"
    configured_root.mkdir(parents=True)
    wrong_root.mkdir()
    config = _guard_config(mountpoint, configured_root)

    monkeypatch.setenv("DP_STORAGE_ROOT", str(wrong_root))
    monkeypatch.setattr(openarm_week1.guard, "load_config", lambda: config)
    with pytest.raises(openarm_week1.ValidationError, match="exact guarded root"):
        openarm_week1.prepare_paths()
    assert not (wrong_root / "outputs").exists()


def test_prepare_paths_rejects_symlinked_guarded_root_before_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountpoint = tmp_path / "mount"
    real_root = tmp_path / "real"
    configured_root = mountpoint / "dp_week1"
    mountpoint.mkdir()
    real_root.mkdir()
    configured_root.symlink_to(real_root, target_is_directory=True)
    config = _guard_config(mountpoint, configured_root)

    monkeypatch.setenv("DP_STORAGE_ROOT", os.fspath(configured_root))
    monkeypatch.setattr(openarm_week1.guard, "load_config", lambda: config)
    monkeypatch.setattr(openarm_week1.guard, "query_mount", lambda expected: {})
    with pytest.raises(openarm_week1.ValidationError, match="storage guard failed"):
        openarm_week1.prepare_paths()
    assert not (real_root / "outputs").exists()
