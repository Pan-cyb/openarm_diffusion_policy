#!/usr/bin/env python3
"""Evaluate the official full image Push-T EMA policy without a workspace."""

from __future__ import annotations

import argparse
import ctypes
import gc
import importlib.metadata
import json
import math
import os
import platform
import random
import re
import resource
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Iterator, TextIO

from guard import GuardError, reject_symlink_components
from fetch_dp_checkpoint import (
    ARTIFACTS,
    CHECKPOINT_FILENAME,
    ArtifactError,
    require_external_storage,
    verify_artifact_receipt,
)


UPSTREAM_COMMIT = "5ba07ac6661db573af695b419a7947ecb704690f"
UPSTREAM_REMOTE = "https://github.com/real-stanford/diffusion_policy.git"
RESERVED_FINAL_SEED = 100_000
EXPECTED_POLICY = {
    "_target_": (
        "diffusion_policy.policy.diffusion_unet_hybrid_image_policy."
        "DiffusionUnetHybridImagePolicy"
    ),
    "horizon": 16,
    "n_action_steps": 8,
    "n_obs_steps": 2,
    "crop_shape": [84, 84],
    "down_dims": [512, 1024, 2048],
    "num_inference_steps": 100,
    "shape_meta.action.shape": [2],
    "shape_meta.obs.image.shape": [3, 96, 96],
    "shape_meta.obs.image.type": "rgb",
    "shape_meta.obs.agent_pos.shape": [2],
    "shape_meta.obs.agent_pos.type": "low_dim",
    "noise_scheduler.num_train_timesteps": 100,
}
EXPECTED_RUNNER_TARGET = (
    "diffusion_policy.env_runner.pusht_image_runner.PushTImageRunner"
)


class EvalError(RuntimeError):
    """Evaluation precondition or output validation failed."""


class Tee:
    def __init__(self, *streams: TextIO):
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)

    @property
    def encoding(self) -> str:
        return self.streams[0].encoding or "utf-8"


@contextmanager
def captured_logs(output_dir: Path) -> Iterator[None]:
    with (
        (output_dir / "stdout.log").open("w", encoding="utf-8", buffering=1) as out,
        (output_dir / "stderr.log").open("w", encoding="utf-8", buffering=1) as err,
        redirect_stdout(Tee(sys.stdout, out)),
        redirect_stderr(Tee(sys.stderr, err)),
    ):
        yield


def _run_git(upstream: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(upstream), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise EvalError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def verify_upstream(project_root: Path) -> dict[str, str]:
    upstream = project_root / "upstream" / "diffusion_policy"
    if not (upstream / ".git").exists():
        raise EvalError(f"official upstream checkout is missing: {upstream}")
    commit = _run_git(upstream, "rev-parse", "HEAD")
    if commit != UPSTREAM_COMMIT:
        raise EvalError(f"upstream commit is {commit}, expected {UPSTREAM_COMMIT}")
    status = _run_git(upstream, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise EvalError(f"upstream checkout is dirty:\n{status}")
    remote = _run_git(upstream, "remote", "get-url", "origin")
    accepted_remotes = {
        UPSTREAM_REMOTE,
        UPSTREAM_REMOTE.removesuffix(".git"),
        "git@github.com:real-stanford/diffusion_policy.git",
    }
    if remote not in accepted_remotes:
        raise EvalError(f"unexpected upstream origin: {remote}")
    return {
        "path": str(upstream.resolve()),
        "commit": commit,
        "status": "clean",
        "origin": remote,
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime_versions(torch: Any) -> dict[str, Any]:
    versions = {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "gym": _distribution_version("gym"),
        "hydra_core": _distribution_version("hydra-core"),
        "omegaconf": _distribution_version("omegaconf"),
        "diffusers": _distribution_version("diffusers"),
        "robomimic": _distribution_version("robomimic"),
        "wandb": _distribution_version("wandb"),
        "dill": _distribution_version("dill"),
    }
    if sys.version_info[:2] != (3, 10):
        raise EvalError(f"Python 3.10 is required, found {versions['python']}")
    if not str(torch.__version__).startswith("1.12.1+cu116"):
        raise EvalError(
            f"torch 1.12.1+cu116 is required, found {torch.__version__}"
        )
    required = {
        "gym": "0.21.0",
        "robomimic": "0.2.0",
    }
    for name, expected in required.items():
        if versions[name] != expected:
            raise EvalError(f"{name} {expected} is required, found {versions[name]}")
    return versions


def _select(cfg: Any, key: str, omega_conf: Any) -> Any:
    return omega_conf.select(cfg, key)


def validate_checkpoint_config(cfg: Any, omega_conf: Any) -> None:
    mismatches = []
    for key, expected in EXPECTED_POLICY.items():
        actual = _select(cfg.policy, key, omega_conf)
        if actual != expected:
            mismatches.append(f"policy.{key}={actual!r}, expected {expected!r}")
    checks = {
        "training.use_ema": True,
        "task.env_runner._target_": EXPECTED_RUNNER_TARGET,
        "task.env_runner.max_steps": 300,
        "task.env_runner.legacy_test": True,
        "task.env_runner.n_action_steps": 8,
        "task.env_runner.n_obs_steps": 2,
    }
    for key, expected in checks.items():
        actual = _select(cfg, key, omega_conf)
        if actual != expected:
            mismatches.append(f"{key}={actual!r}, expected {expected!r}")
    if mismatches:
        raise EvalError("checkpoint config is not the selected official architecture:\n"
                        + "\n".join(mismatches))


def _fadvise(
    stream: Any,
    advice_name: str,
    *,
    offset: int = 0,
    length: int = 0,
) -> None:
    advice = getattr(os, advice_name, None)
    fadvise = getattr(os, "posix_fadvise", None)
    if advice is None or fadvise is None:
        return
    try:
        fadvise(stream.fileno(), offset, length, advice)
    except OSError:
        pass


class CacheDroppingReader:
    """File wrapper that advises the kernel to drop each consumed range."""

    def __init__(self, stream: Any):
        self.stream = stream

    def read(self, size: int = -1) -> bytes:
        offset = self.stream.tell()
        data = self.stream.read(size)
        if data:
            _fadvise(
                self.stream,
                "POSIX_FADV_DONTNEED",
                offset=offset,
                length=len(data),
            )
        return data

    def readinto(self, buffer: Any) -> int:
        offset = self.stream.tell()
        count = self.stream.readinto(buffer)
        if count:
            _fadvise(
                self.stream,
                "POSIX_FADV_DONTNEED",
                offset=offset,
                length=count,
            )
        return count

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


def _release_host_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (AttributeError, OSError):
        pass


def load_ema_only(
    checkpoint: Path,
    *,
    torch: Any,
    dill: Any,
) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    with checkpoint.open("rb") as stream:
        _fadvise(stream, "POSIX_FADV_SEQUENTIAL")
        cache_dropping_stream = CacheDroppingReader(stream)
        try:
            payload = torch.load(
                cache_dropping_stream,
                map_location="cpu",
                pickle_module=dill,
            )
        finally:
            _fadvise(stream, "POSIX_FADV_DONTNEED")
    if not isinstance(payload, dict):
        raise EvalError("checkpoint payload is not a dictionary")
    try:
        cfg = payload.pop("cfg")
        state_dicts = payload.pop("state_dicts")
        ema_state = state_dicts.pop("ema_model")
    except (KeyError, AttributeError) as exc:
        raise EvalError("checkpoint has no cfg/EMA state_dict payload") from exc
    state_dicts.clear()
    payload.clear()
    del state_dicts
    del payload
    _release_host_memory()
    return (cfg, ema_state), {"checkpoint_load_seconds": time.perf_counter() - started}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "_path"):
        return str(value._path)
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise EvalError(f"runner returned non-JSON value of type {type(value).__name__}")


def _write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _extract_seed_results(metrics: dict[str, Any], n_test: int) -> list[dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    score_pattern = re.compile(r"^test/sim_max_reward_(\d+)$")
    video_pattern = re.compile(r"^test/sim_video_(\d+)$")
    for key, value in metrics.items():
        score_match = score_pattern.match(key)
        if score_match:
            seed = int(score_match.group(1))
            score = float(value)
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise EvalError(f"invalid Push-T score for seed {seed}: {score}")
            results.setdefault(seed, {"seed": seed, "video_path": None})[
                "max_reward_score"
            ] = score
            continue
        video_match = video_pattern.match(key)
        if video_match:
            seed = int(video_match.group(1))
            results.setdefault(seed, {"seed": seed})["video_path"] = str(value)
    ordered = [results[seed] for seed in sorted(results)]
    scored = [item for item in ordered if "max_reward_score" in item]
    if len(scored) != n_test:
        raise EvalError(f"runner returned {len(scored)} seed scores, expected {n_test}")
    return scored


def _inspect_videos(
    seed_results: list[dict[str, Any]],
    *,
    n_test_vis: int,
    av: Any,
) -> list[dict[str, Any]]:
    inspected = []
    with_video = [item for item in seed_results if item.get("video_path")]
    if len(with_video) != n_test_vis:
        raise EvalError(
            f"runner returned {len(with_video)} videos, expected {n_test_vis}"
        )
    for result in seed_results:
        path_value = result.get("video_path")
        if not path_value:
            continue
        path = Path(path_value)
        if not path.is_file() or path.stat().st_size <= 0:
            raise EvalError(f"video is missing or empty for seed {result['seed']}: {path}")
        try:
            with av.open(str(path), mode="r") as container:
                if not container.streams.video:
                    raise EvalError(f"video has no video stream: {path}")
                stream = container.streams.video[0]
                frame_count = sum(1 for _ in container.decode(stream))
                average_rate = (
                    float(stream.average_rate) if stream.average_rate else None
                )
                codec = stream.codec_context.name
        except EvalError:
            raise
        except Exception as exc:
            raise EvalError(f"cannot decode video {path}: {exc}") from exc
        if frame_count <= 0:
            raise EvalError(f"video decoded zero frames: {path}")
        if average_rate is None or average_rate <= 0:
            raise EvalError(f"video has no positive decoded frame rate: {path}")
        duration_seconds = frame_count / average_rate
        if duration_seconds <= 0:
            raise EvalError(f"video has no positive decoded duration: {path}")
        validation = {
            "seed": result["seed"],
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "decoded_frame_count": frame_count,
            "average_fps": average_rate,
            "decoded_duration_seconds": duration_seconds,
            "codec": codec,
            "nonempty_media": True,
            "episode_note": (
                "The original environment may terminate before the 300-step cap "
                "on task success."
            ),
        }
        result["video_validation"] = validation
        inspected.append(validation)
    return inspected


def _device_stats(torch: Any, device: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "host_max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "cuda_peak_allocated_bytes": None,
        "cuda_peak_reserved_bytes": None,
        "cuda_device": None,
    }
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        stats.update(
            {
                "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
                "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
                "cuda_device": torch.cuda.get_device_name(device),
            }
        )
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Week 1 development evaluation of the official full image Push-T "
            "Diffusion Policy checkpoint. This is not paper benchmark reproduction."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--n-test", type=int, default=3)
    parser.add_argument("--n-test-vis", type=int)
    parser.add_argument("--test-start-seed", type=int, default=10_000)
    parser.add_argument("--rng-seed", type=int, default=42)
    return parser.parse_args(argv)


def _validated_output_dir(raw_output: Path, storage_root: Path) -> Path:
    expanded = raw_output.expanduser()
    if not expanded.is_absolute():
        raise EvalError("--output-dir must be an absolute path")
    if any(part in (".", "..") for part in expanded.parts):
        raise EvalError("--output-dir must not contain '.' or '..' components")
    try:
        reject_symlink_components(expanded)
    except GuardError as exc:
        raise EvalError(f"unsafe output path: {exc}") from exc
    if expanded.exists() or expanded.is_symlink():
        raise EvalError(f"output directory already exists; refusing reuse: {expanded}")
    try:
        parent = expanded.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise EvalError(
            f"output parent must already exist for strict validation: {expanded.parent}"
        ) from exc
    output_dir = parent / expanded.name
    try:
        output_dir.relative_to(storage_root)
    except ValueError as exc:
        raise EvalError(
            f"output directory must be below guarded DP_STORAGE_ROOT {storage_root}"
        ) from exc
    if output_dir == storage_root:
        raise EvalError("output directory must be a new child below DP_STORAGE_ROOT")
    return output_dir


def run(
    args: argparse.Namespace,
    output_dir: Path,
    storage_root: Path,
) -> None:
    total_started = time.perf_counter()
    if not 1 <= args.n_test <= 50:
        raise EvalError("--n-test must be in 1..50")
    n_test_vis = min(args.n_test, 4) if args.n_test_vis is None else args.n_test_vis
    if not 1 <= n_test_vis <= args.n_test:
        raise EvalError("--n-test-vis must be in 1..n-test so media is validated")
    if args.test_start_seed < 0:
        raise EvalError("--test-start-seed must be non-negative")
    if args.test_start_seed + args.n_test > RESERVED_FINAL_SEED:
        raise EvalError(
            "seed range reaches 100000, which is reserved for final evaluation"
        )

    project_root = Path(__file__).resolve().parents[1]
    checkpoint = (
        args.checkpoint.expanduser()
        if args.checkpoint is not None
        else storage_root / "checkpoints" / CHECKPOINT_FILENAME
    )
    checkpoint = checkpoint.resolve(strict=False)
    try:
        checkpoint.relative_to(storage_root)
    except ValueError as exc:
        raise EvalError("checkpoint must be located below DP_STORAGE_ROOT") from exc
    receipt = verify_artifact_receipt(checkpoint, ARTIFACTS["checkpoint"])
    upstream = verify_upstream(project_root)
    sys.dont_write_bytecode = True
    sys.path.insert(0, upstream["path"])

    import dill
    import hydra
    import numpy as np
    import torch
    import av
    from omegaconf import OmegaConf

    versions = runtime_versions(torch)
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise EvalError(f"CUDA device requested but unavailable: {device}")
    torch.set_num_threads(2)
    if device.type == "cuda":
        torch.cuda.set_per_process_memory_fraction(0.65, device)

    random.seed(args.rng_seed)
    np.random.seed(args.rng_seed)
    torch.manual_seed(args.rng_seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.rng_seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)

    (cfg, ema_state), timings = load_ema_only(
        checkpoint,
        torch=torch,
        dill=dill,
    )
    validate_checkpoint_config(cfg, OmegaConf)
    OmegaConf.save(cfg, output_dir / "config.original.yaml", resolve=False)
    OmegaConf.save(cfg, output_dir / "config.resolved.yaml", resolve=True)
    effective_cfg = OmegaConf.create(OmegaConf.to_container(cfg, resolve=False))
    effective_cfg.task.env_runner.n_envs = 1
    effective_cfg.task.env_runner.n_train = 0
    effective_cfg.task.env_runner.n_train_vis = 0
    effective_cfg.task.env_runner.n_test = args.n_test
    effective_cfg.task.env_runner.n_test_vis = n_test_vis
    effective_cfg.task.env_runner.test_start_seed = args.test_start_seed
    effective_cfg.task.env_runner.max_steps = 300
    effective_cfg.evaluation = {
        "scope": "Week 1 resource-adapted official pretrained evaluation",
        "device": str(device),
        "rng_seed": args.rng_seed,
        "torch_num_threads": 2,
        "cuda_memory_fraction": 0.65 if device.type == "cuda" else None,
    }
    OmegaConf.save(
        effective_cfg,
        output_dir / "config.effective_eval.yaml",
        resolve=True,
    )

    construct_started = time.perf_counter()
    policy = hydra.utils.instantiate(cfg.policy)
    incompatible = policy.load_state_dict(ema_state, strict=True)
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise EvalError(
            f"EMA state mismatch: missing={incompatible.missing_keys}, "
            f"unexpected={incompatible.unexpected_keys}"
        )
    del ema_state
    _release_host_memory()
    policy.to(device)
    policy.eval()
    timings["policy_construct_load_seconds"] = time.perf_counter() - construct_started

    runner = hydra.utils.instantiate(
        effective_cfg.task.env_runner,
        output_dir=str(output_dir),
    )
    rollout_started = time.perf_counter()
    try:
        runner_log = runner.run(policy)
    finally:
        env = getattr(runner, "env", None)
        if env is not None:
            env.close()
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    timings["rollout_seconds"] = time.perf_counter() - rollout_started
    timings["total_wall_seconds"] = time.perf_counter() - total_started

    json_metrics = _json_value(runner_log)
    _write_json(output_dir / "eval_log.json", json_metrics)
    seed_results = _extract_seed_results(json_metrics, args.n_test)
    expected_seeds = list(range(args.test_start_seed, args.test_start_seed + args.n_test))
    if [item["seed"] for item in seed_results] != expected_seeds:
        raise EvalError("runner results do not match the requested development seeds")
    video_validation = _inspect_videos(
        seed_results,
        n_test_vis=n_test_vis,
        av=av,
    )
    upstream_after = verify_upstream(project_root)
    report = {
        "schema_version": 1,
        "scope": {
            "phase": "Week 1 development evaluation",
            "task": "official image Push-T pretrained checkpoint",
            "execution_path": (
                "resource-adapted official pretrained evaluation; not the "
                "unchanged upstream eval.py workspace path"
            ),
            "original_policy_architecture_and_runner": True,
            "paper_benchmark_reproduction": False,
            "training_performed": False,
            "training_data_used": False,
            "metric_interpretation": (
                "PushTImageRunner max-reward score; a numeric score is not "
                "automatically a binary success rate."
            ),
        },
        "deviations_from_published_config": {
            "n_envs": 1,
            "n_train": 0,
            "n_test": args.n_test,
            "n_test_vis": n_test_vis,
            "test_start_seed": args.test_start_seed,
            "published_test_start_seed_reserved": RESERVED_FINAL_SEED,
            "max_steps": 300,
            "n_test_vis_minimum": 1,
            "checkpoint_loading": (
                "map_location=cpu; retain cfg and EMA state only; instantiate "
                "the original cfg.policy after releasing other payload state"
            ),
            "algorithm_monkeypatches": [],
        },
        "checkpoint": {
            "path": str(checkpoint),
            "source_url": receipt["source_url"],
            "size": receipt["size"],
            "sha256": receipt["sha256"],
            "sha256_provenance": receipt["sha256_provenance"],
        },
        "upstream": {
            "before": upstream,
            "after": upstream_after,
            "verified_clean_before_and_after": upstream == upstream_after,
        },
        "runtime": versions,
        "rng": {
            "python_random": args.rng_seed,
            "numpy": args.rng_seed,
            "torch": args.rng_seed,
        },
        "device": str(device),
        "resource_controls": {
            "torch_num_threads": torch.get_num_threads(),
            "cuda_memory_fraction": 0.65 if device.type == "cuda" else None,
        },
        "timing": timings,
        "memory": _device_stats(torch, device),
        "per_seed_results": seed_results,
        "video_validation": video_validation,
        "effective_config_path": str(
            output_dir / "config.effective_eval.yaml"
        ),
        "original_metrics_path": str(output_dir / "eval_log.json"),
        "original_metrics": json_metrics,
    }
    _write_json(output_dir / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        storage_root = require_external_storage()
        output_dir = _validated_output_dir(args.output_dir, storage_root)
        output_dir.mkdir(parents=True, exist_ok=False)
    except (ArtifactError, EvalError, OSError) as exc:
        print(f"ERROR: cannot prepare output directory: {exc}", file=sys.stderr)
        return 1

    with captured_logs(output_dir):
        try:
            run(args, output_dir, storage_root)
        except BaseException as exc:
            traceback.print_exc()
            failure = {
                "status": "failed",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            }
            try:
                _write_json(output_dir / "failure.json", failure)
            except OSError:
                traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
