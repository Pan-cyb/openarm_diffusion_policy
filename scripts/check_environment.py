#!/usr/bin/env python3
"""Validate and record the minimal standalone DP Week1 environment."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "upstream-pins.json"
OUTPUT_PATH = ROOT / "outputs" / "environment.json"
UV = Path("/home/pan/.local/bin/uv")
LOCAL_UV_CACHE = ROOT / ".cache" / "uv"

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("WANDB_MODE", "disabled")
os.environ.setdefault("MUJOCO_GL", "egl")
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")


EXPECTED_PACKAGES = {
    "torch": "1.12.1+cu116",
    "torchvision": "0.13.1+cu116",
    "numpy": "1.23.5",
    "scipy": "1.9.3",
    "diffusers": "0.11.1",
    "huggingface-hub": "0.16.4",
    "hydra-core": "1.3.2",
    "gym": "0.21.0",
    "dill": "0.3.5.1",
    "einops": "0.4.1",
    "zarr": "2.12.0",
    "numcodecs": "0.10.2",
    "pygame": "2.1.2",
    "pymunk": "6.2.1",
    "Shapely": "1.8.4",
    "imageio": "2.22.0",
    "imageio-ffmpeg": "0.4.7",
    "scikit-image": "0.19.3",
    "scikit-video": "1.1.11",
    "opencv-python": "4.6.0.66",
    "wandb": "0.13.3",
    "protobuf": "3.19.6",
    "h5py": "3.7.0",
    "pandas": "1.5.3",
    "psutil": "5.9.2",
    "tqdm": "4.64.1",
    "click": "8.0.4",
    "termcolor": "2.0.1",
    "tensorboard": "2.10.1",
    "tensorboardX": "2.5.1",
    "matplotlib": "3.6.1",
    "mujoco": "3.6.0",
    "pytest": "8.3.5",
    "packaging": "23.2",
    "setuptools": "65.5.0",
    "wheel": "0.38.4",
    "cloudpickle": "2.2.1",
    "egl-probe": "1.0.2",
    "av": "12.3.0",
    "robomimic": "0.2.0",
    "openarm-mujoco": "2.3.0",
}


def command(
    args: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return command(["git", "-C", str(target), *args])


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def package_versions() -> dict[str, str]:
    versions = {
        name: importlib.metadata.version(name)
        for name in EXPECTED_PACKAGES
    }
    mismatches = {
        name: {"actual": versions[name], "expected": expected}
        for name, expected in EXPECTED_PACKAGES.items()
        if versions[name] != expected
    }
    if mismatches:
        raise RuntimeError(f"package version mismatches: {mismatches}")
    return versions


def installed_distributions() -> list[dict[str, Any]]:
    # Keep a list rather than a name-keyed mapping so duplicate metadata is visible.
    return sorted(
        (
            {
                "name": distribution.metadata["Name"],
                "version": distribution.version,
                "requires_dist": distribution.requires or [],
            }
            for distribution in importlib.metadata.distributions()
        ),
        key=lambda item: (str(item["name"]).lower(), item["version"]),
    )


def compatibility_profile() -> dict[str, Any]:
    pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    dp_pin = pins["sources"]["diffusion_policy"]
    declaration = git(
        ROOT / dp_pin["destination"],
        "show",
        f"{dp_pin['commit']}:conda_environment.yaml",
    )
    if declaration.returncode != 0:
        raise RuntimeError(
            "cannot read pinned upstream dependency declaration: "
            + declaration.stderr.strip()
        )
    return {
        "exact_upstream_conda_environment": False,
        "profile": "Minimal local Week1 compatibility environment",
        "python": {
            "upstream_declared": "3.9",
            "local_target": "3.10",
            "actual": platform.python_version(),
        },
        "upstream_declared_dependencies": {
            "repository": dp_pin["repository"],
            "commit": dp_pin["commit"],
            "path": "conda_environment.yaml",
            "contents": declaration.stdout,
        },
        "local_requirements": (ROOT / "requirements-dp.txt").read_text(
            encoding="utf-8"
        ),
        "selected_dependency_deviations": pins.get("dependency_deviations", {}),
        "installed_versions_location": "checks.installed_distributions.details",
        "algorithm_policy": (
            "Local Python and dependency compatibility changes only; no DP "
            "algorithm or architecture modification is authorized. The source_pins "
            "check separately verifies unchanged pinned DP/OpenArm source and "
            "the sole recorded Gym installation-metadata patch."
        ),
    }


def uv_metadata_check() -> dict[str, Any]:
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(LOCAL_UV_CACHE)
    environment["UV_LINK_MODE"] = "copy"
    result = subprocess.run(
        [str(UV), "pip", "check", "--python", sys.executable],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or result.stderr.strip())
    return {
        "command": [str(UV), "pip", "check", "--python", sys.executable],
        "stdout": result.stdout.strip(),
    }


def source_check() -> dict[str, Any]:
    pins = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    result: dict[str, Any] = {}
    for name, pin in pins["sources"].items():
        target = ROOT / pin["destination"]
        origin_result = git(target, "remote", "get-url", "origin")
        head_result = git(target, "rev-parse", "HEAD")
        if origin_result.returncode or head_result.returncode:
            raise RuntimeError(f"{name} is not a readable Git checkout")
        origin = origin_result.stdout.strip().rstrip("/")
        head = head_result.stdout.strip()
        if origin != pin["repository"].rstrip("/") or head != pin["commit"]:
            raise RuntimeError(
                f"{name} source mismatch: origin={origin}, commit={head}"
            )

        changed = git(target, "diff", "--name-only", "HEAD", "--").stdout.splitlines()
        staged = git(target, "diff", "--cached", "--name-only").stdout.splitlines()
        untracked = git(
            target, "ls-files", "--others", "--exclude-standard"
        ).stdout.splitlines()
        allowed_patch = pin.get("allow_worktree_patch")
        if allowed_patch is None:
            if changed or staged or untracked:
                raise RuntimeError(f"{name} source checkout is modified")
            worktree = "clean"
        else:
            patch_path = ROOT / allowed_patch
            actual = git(
                target,
                "diff",
                "--no-ext-diff",
                "--no-color",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                "--",
                "setup.py",
            ).stdout
            expected = patch_path.read_text(encoding="utf-8")
            if changed != ["setup.py"] or staged or untracked or actual != expected:
                raise RuntimeError(f"{name} differs from the exact recorded patch")
            worktree = f"exact patch only: {allowed_patch}"
        result[name] = {
            "path": str(target),
            "origin": origin,
            "commit": head,
            "worktree": worktree,
        }
    return result


def required_imports() -> list[str]:
    names = [
        "av",
        "cv2",
        "diffusers",
        "dill",
        "einops",
        "egl_probe",
        "gym",
        "h5py",
        "huggingface_hub",
        "hydra",
        "imageio",
        "matplotlib",
        "mujoco",
        "numcodecs",
        "numpy",
        "omegaconf",
        "openarm_mujoco",
        "packaging",
        "pandas",
        "psutil",
        "pygame",
        "pymunk",
        "robomimic",
        "scipy",
        "shapely",
        "skimage",
        "skvideo",
        "tensorboard",
        "tensorboardX",
        "torch",
        "torchvision",
        "wandb",
        "zarr",
        "diffusion_policy.env_runner.pusht_image_runner",
        "diffusion_policy.policy.diffusion_unet_hybrid_image_policy",
    ]
    for name in names:
        importlib.import_module(name)
    return names


def gym_shared_memory_check() -> dict[str, Any]:
    from gym import spaces
    from gym.vector.utils import (
        CloudpickleWrapper,
        clear_mpi_env_vars,
        concatenate,
        create_empty_array,
        create_shared_memory,
        read_from_shared_memory,
        write_to_shared_memory,
    )
    import numpy as np
    from packaging.requirements import Requirement

    requirements = importlib.metadata.requires("gym") or []
    for requirement in requirements:
        Requirement(requirement)

    space = spaces.Dict(
        {
            "image": spaces.Box(0.0, 1.0, shape=(3, 4, 4), dtype=np.float32),
            "agent_pos": spaces.Box(0.0, 512.0, shape=(2,), dtype=np.float32),
        }
    )
    shared = create_shared_memory(space, n=1, ctx=mp.get_context("spawn"))
    sample = {
        "image": np.ones((3, 4, 4), dtype=np.float32),
        "agent_pos": np.array([1.0, 2.0], dtype=np.float32),
    }
    write_to_shared_memory(0, sample, shared, space)
    observed = read_from_shared_memory(shared, space, n=1)
    if not np.array_equal(observed["image"][0], sample["image"]):
        raise RuntimeError("Gym shared-memory image round trip failed")
    empty = create_empty_array(space, n=1)
    concatenate([sample], empty, space)
    if not np.array_equal(empty["agent_pos"][0], sample["agent_pos"]):
        raise RuntimeError("Gym concatenate API round trip failed")
    with clear_mpi_env_vars():
        wrapped = CloudpickleWrapper(lambda: 1)
        if wrapped() != 1:
            raise RuntimeError("Gym CloudpickleWrapper failed")
    return {
        "version": importlib.metadata.version("gym"),
        "requires_dist": requirements,
        "shared_memory_round_trip": True,
        "required_vector_symbols": True,
    }


def pusht_check() -> dict[str, Any]:
    import numpy as np
    from diffusion_policy.env.pusht.pusht_image_env import PushTImageEnv
    from diffusion_policy.env_runner.pusht_image_runner import PushTImageRunner
    from diffusion_policy.policy.diffusion_unet_hybrid_image_policy import (
        DiffusionUnetHybridImagePolicy,
    )

    environment = PushTImageEnv(render_size=96)
    try:
        observation = environment.reset()
        image = observation["image"]
        agent_pos = observation["agent_pos"]
        if image.shape != (3, 96, 96) or image.dtype != np.float32:
            raise RuntimeError(f"unexpected Push-T image observation: {image.shape}")
        if agent_pos.shape != (2,):
            raise RuntimeError(f"unexpected Push-T agent position: {agent_pos.shape}")
        frame = environment.render(mode="rgb_array")
        if frame.shape != (96, 96, 3) or frame.dtype != np.uint8:
            raise RuntimeError(f"unexpected Push-T render frame: {frame.shape}")
    finally:
        environment.close()
    return {
        "runner": f"{PushTImageRunner.__module__}.{PushTImageRunner.__name__}",
        "policy": (
            f"{DiffusionUnetHybridImagePolicy.__module__}."
            f"{DiffusionUnetHybridImagePolicy.__name__}"
        ),
        "observation_shapes": {"image": [3, 96, 96], "agent_pos": [2]},
        "render_shape": [96, 96, 3],
    }


def cuda_check() -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is false")
    if torch.version.cuda != "11.6":
        raise RuntimeError(f"expected torch CUDA 11.6, found {torch.version.cuda}")
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    device = torch.device("cuda:0")
    model = torch.nn.Sequential(
        torch.nn.Conv2d(3, 16, kernel_size=3, padding=1),
        torch.nn.SiLU(),
        torch.nn.AdaptiveAvgPool2d((1, 1)),
    ).to(device)
    sample = torch.zeros((1, 3, 96, 96), device=device)
    output = model(sample)
    torch.cuda.synchronize()
    result = {
        "torch_cuda_version": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(device),
        "device_capability": list(torch.cuda.get_device_capability(device)),
        "sample_shape": list(sample.shape),
        "output_shape": list(output.shape),
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "total_memory_bytes": torch.cuda.get_device_properties(device).total_memory,
    }
    del output, sample, model
    torch.cuda.empty_cache()
    return result


def mujoco_headless_check() -> dict[str, Any]:
    import mujoco

    scene_path = ROOT / "upstream" / "openarm_mujoco" / "v1" / "scene.xml"
    model = mujoco.MjModel.from_xml_path(str(scene_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    renderer = mujoco.Renderer(model, height=32, width=32)
    try:
        renderer.update_scene(data)
        pixels = renderer.render()
        if pixels.shape != (32, 32, 3):
            raise RuntimeError(f"unexpected MuJoCo frame shape: {pixels.shape}")
    finally:
        renderer.close()
    return {
        "version": mujoco.__version__,
        "gl_backend": os.environ.get("MUJOCO_GL"),
        "openarm_scene": str(scene_path),
        "openarm_dimensions": {
            "nq": model.nq,
            "nv": model.nv,
            "nu": model.nu,
            "ncam": model.ncam,
        },
        "render_shape": [32, 32, 3],
    }


def local_storage_check() -> dict[str, str]:
    result = command(["findmnt", "-T", str(ROOT), "-n", "-o", "FSTYPE"])
    if result.returncode != 0 or result.stdout.strip() != "ext4":
        raise RuntimeError(f"project is not on ext4: {result.stdout.strip()}")
    venv_result = command(
        ["findmnt", "-T", str(Path(sys.executable).resolve()), "-n", "-o", "FSTYPE"]
    )
    if venv_result.returncode != 0 or venv_result.stdout.strip() != "ext4":
        raise RuntimeError("virtual environment is not on local ext4")
    cache_probe = LOCAL_UV_CACHE if LOCAL_UV_CACHE.exists() else ROOT
    cache_result = command(
        ["findmnt", "-T", str(cache_probe), "-n", "-o", "FSTYPE"]
    )
    if cache_result.returncode != 0 or cache_result.stdout.strip() != "ext4":
        raise RuntimeError("uv build/cache scratch is not on local ext4")
    return {
        "project_filesystem": result.stdout.strip(),
        "venv_filesystem": venv_result.stdout.strip(),
        "uv_cache_filesystem": cache_result.stdout.strip(),
        "uv_cache_path": str(LOCAL_UV_CACHE),
    }


def run_check(
    report: dict[str, Any], name: str, function: Callable[[], Any]
) -> None:
    started = time.monotonic()
    try:
        details = function()
    except Exception as exc:
        report["checks"][name] = {
            "passed": False,
            "elapsed_seconds": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }
    else:
        report["checks"][name] = {
            "passed": True,
            "elapsed_seconds": time.monotonic() - started,
            "details": details,
        }


def main() -> int:
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": (
            "Minimal standalone DP Week1 environment for the original image "
            "Push-T policy and headless OpenArm MuJoCo diagnostics; not a full "
            "all-benchmark, training, paper-reproduction, pi0, or real-robot environment."
        ),
        "excluded": [
            "pytorch3d",
            "r3m",
            "real-robot SDKs",
            "pi0/OpenPI",
            "Ray",
            "pybullet",
            "robosuite",
            "dm-control",
            "legacy mujoco-py",
        ],
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "environment": {
            key: os.environ.get(key)
            for key in (
                "DP_STORAGE_ROOT",
                "UV_CACHE_DIR",
                "UV_LINK_MODE",
                "MUJOCO_GL",
                "PYOPENGL_PLATFORM",
                "SDL_VIDEODRIVER",
            )
        },
        "storage_policy": {
            "local_ext4": [
                str(ROOT / "upstream"),
                str(ROOT / ".venv-dp"),
                str(LOCAL_UV_CACHE),
            ],
            "external_ext4_via_guard": [
                "large checkpoints",
                "videos",
                "non-symlink-dependent persistent artifacts",
            ],
            "scratch_exception": (
                "Python virtual environments and uv build/cache scratch remain on "
                "local ext4; the external ext4 destination is reserved for guarded "
                "large persistent artifacts. The project-owned cache may be removed "
                "only with the installer's --clean-local-uv-cache option after "
                "successful verification."
            ),
        },
        "checks": {},
        "created_at_unix": time.time(),
    }

    checks = (
        ("installed_distributions", installed_distributions),
        ("compatibility_profile", compatibility_profile),
        ("local_storage", local_storage_check),
        ("source_pins", source_check),
        ("package_versions", package_versions),
        ("uv_pip_check", uv_metadata_check),
        ("required_imports", required_imports),
        ("gym_0_21_vector_api", gym_shared_memory_check),
        ("original_pusht_image_path", pusht_check),
        ("cuda_model_and_sample", cuda_check),
        ("headless_mujoco", mujoco_headless_check),
    )
    for name, function in checks:
        run_check(report, name, function)

    report["passed"] = all(item["passed"] for item in report["checks"].values())
    atomic_json(OUTPUT_PATH, report)
    print(json.dumps({
        "passed": report["passed"],
        "report": str(OUTPUT_PATH),
        "checks": {
            name: {"passed": result["passed"], "error": result.get("error")}
            for name, result in report["checks"].items()
        },
    }, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
