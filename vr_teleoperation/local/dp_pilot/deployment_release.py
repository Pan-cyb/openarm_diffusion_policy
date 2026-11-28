"""Pinned deployment release selection and offline validation. No device access."""
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time

import numpy as np


HERE = Path(__file__).resolve().parent
OUTPUTS = HERE.parents[1] / "outputs"
LEGACY_ROOT = OUTPUTS / "dp_baseline49_20261123"
NEW_ROOT = OUTPUTS / "dp_20261128_202032"
SELECT_ENV = "OPENARM_DP_RELEASE"
VIEWS = ("global", "chest", "wrist_left", "wrist_right")
LEGACY_WEIGHT_SHA = "3b2f300753ef66d3d80ac1794f87c2c7dee031721b80de0e7721f66188b6add6"
LEGACY_POSE_SHA = "4cfca0bab1f702b5442912def52c295e2961f6a21909fb4329a7462e54787b09"
RELEASE_FIELDS = {
    "arch", "checkpoint_sha256", "manifest_sha256", "episode", "frame", "pose_sha256"
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pose_sha256(row):
    return hashlib.sha256(np.asarray(row, dtype="<f4").tobytes()).hexdigest()


@dataclass(frozen=True)
class DeploymentRelease:
    arch: str
    checkpoint: Path
    data: Path
    manifest: dict
    episode: int
    frame: int
    checkpoint_sha256: str
    manifest_sha256: str
    pose_sha256: str
    legacy: bool = False

    @property
    def demo(self):
        return self.data / f"ep{self.episode}.npz"

    def record(self):
        return {
            "arch": self.arch,
            "checkpoint": str(self.checkpoint),
            "data": str(self.data),
            "checkpoint_sha256": self.checkpoint_sha256,
            "manifest_sha256": self.manifest_sha256,
            "episode": self.episode,
            "frame": self.frame,
            "pose_sha256": self.pose_sha256,
            "legacy": self.legacy,
        }


def _validate_data(release):
    manifest = release.manifest
    if manifest.get("camera_order") != list(VIEWS) or manifest.get("hz") != 10:
        raise ValueError("release manifest camera order or rate is not deployable")
    with np.load(release.demo, allow_pickle=False) as episode:
        required = {"time_ns", "rgb", "obs", "action"}
        if not required.issubset(episode.files):
            raise ValueError("release episode is missing deployment arrays")
        size = len(episode["obs"])
        if not 1 <= release.frame < size:
            raise ValueError("release start frame needs one preceding 10 Hz observation")
        if int(episode["time_ns"][release.frame]) - int(
                episode["time_ns"][release.frame - 1]) != 100_000_000:
            raise ValueError("release start history is not on the 10 Hz grid")
        row = episode["obs"][release.frame]
        rgb = episode["rgb"][release.frame]
        if row.shape != (16,) or rgb.shape != (4, 54, 96, 3):
            raise ValueError("release start observation has unexpected shape")
        if not np.isfinite(row).all() or pose_sha256(row) != release.pose_sha256:
            raise ValueError("release start pose does not match pose_sha256")


def legacy_release():
    data = LEGACY_ROOT / "data"
    return DeploymentRelease(
        arch="legacy-cnn",
        checkpoint=LEGACY_ROOT / "run/pilot.pt",
        data=data,
        manifest={},
        episode=12,
        frame=12,
        checkpoint_sha256=LEGACY_WEIGHT_SHA,
        manifest_sha256="",
        pose_sha256=LEGACY_POSE_SHA,
        legacy=True,
    )


def new_release(arch, root=None, verify=True):
    root = NEW_ROOT if root is None else root
    if arch not in ("cnn", "transformer"):
        raise ValueError("deployment architecture must be cnn or transformer")
    data = root / "data"
    checkpoint = root / arch / "pilot.pt"
    release_path = root / arch / "release.json"
    manifest_path = data / "manifest.json"
    pin = json.loads(release_path.read_text())
    if set(pin) != RELEASE_FIELDS or pin["arch"] != arch:
        raise ValueError("release.json fields or architecture do not match the selected release")
    for name in ("checkpoint_sha256", "manifest_sha256", "pose_sha256"):
        if not isinstance(pin[name], str) or not SHA256.fullmatch(pin[name]):
            raise ValueError(f"release.json has an invalid {name}")
    if type(pin["episode"]) is not int or type(pin["frame"]) is not int:
        raise ValueError("release episode and frame must be integers")
    release = DeploymentRelease(
        arch=arch,
        checkpoint=checkpoint,
        data=data,
        manifest=json.loads(manifest_path.read_text()),
        episode=pin["episode"],
        frame=pin["frame"],
        checkpoint_sha256=pin["checkpoint_sha256"],
        manifest_sha256=pin["manifest_sha256"],
        pose_sha256=pin["pose_sha256"],
    )
    if verify:
        if file_sha256(checkpoint) != pin["checkpoint_sha256"]:
            raise ValueError("selected checkpoint does not match release.json")
        if file_sha256(manifest_path) != pin["manifest_sha256"]:
            raise ValueError("shared manifest does not match release.json")
        _validate_data(release)
    return release


def selected_release():
    selected = os.environ.get(SELECT_ENV)
    return legacy_release() if selected is None else new_release(selected, verify=False)


def reference_images(release, episode=None, frame=None):
    """Load and verify all four original images for a release observation."""
    import cv2

    episode = release.episode if episode is None else episode
    frame = release.frame if frame is None else frame
    if type(episode) is not int or type(frame) is not int or frame < 0:
        raise ValueError("invalid reference frame")
    with np.load(release.data / f"ep{episode}.npz", allow_pickle=False) as archive:
        anchor = int(archive["time_ns"][frame])
        small = archive["rgb"][frame]
        q = archive["obs"][frame].copy()
    manifest = release.manifest or json.loads((release.data / "manifest.json").read_text())
    if manifest.get("camera_order") != list(VIEWS):
        raise ValueError("reference manifest view order does not match deployment")
    folder = Path(manifest["source"]) / f"episodes/{episode}/cameras"
    result = {}
    for index, name in enumerate(VIEWS):
        files = sorted((folder / name).glob("*.jpeg"), key=lambda path: int(path.stem))
        stamps = [int(path.stem) for path in files]
        selected = np.searchsorted(stamps, anchor, side="right") - 1
        if selected < 0 or anchor - stamps[selected] >= 100_000_000:
            raise ValueError(f"missing release reference: {name}")
        image = cv2.imread(str(files[selected]))
        if image is None or image.shape != (720, 1280, 3) or not np.array_equal(
                cv2.resize(image, (96, 54), interpolation=cv2.INTER_AREA)[:, :, ::-1],
                small[index]):
            raise ValueError(f"release reference does not match training pixels: {name}")
        result[name] = image
    if set(result) != set(VIEWS):
        raise ValueError("release does not provide all four reference views")
    if (episode, frame) == (release.episode, release.frame) and (
            q.shape != (16,) or pose_sha256(q) != release.pose_sha256):
        raise ValueError("release reference pose does not match pose_sha256")
    return result, q


def smoke_prediction(release):
    """Run one real 32-step DDPM prediction from the pinned start history."""
    import torch
    from shadow import ShadowPolicy

    torch.set_num_threads(2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    policy = ShadowPolicy(
        release.checkpoint, device, use_graph=device == "cuda", release=release)
    if device == "cuda":
        torch.cuda.synchronize()
    initialized = time.perf_counter()
    with np.load(release.demo, allow_pickle=False) as episode:
        history = slice(release.frame - 1, release.frame + 1)
        stamps = episode["time_ns"][history]
        rgb = episode["rgb"][history]
        qpos = episode["obs"][history]
    generator = torch.Generator(device=device).manual_seed(724)  # same initial seed as predict_live
    durations, first = [], None
    for _ in range(10):
        tick = time.perf_counter()
        result = policy.predict(stamps, rgb, qpos, generator)
        if device == "cuda":
            torch.cuda.synchronize()
        durations.append((time.perf_counter()-tick)*1000)
        if result.shape != (8, 16) or not np.isfinite(result).all():
            raise ValueError("release smoke prediction is invalid")
        if first is None:
            first = result[0].astype(float)
    return {
        "device": device,
        "cuda_graph": device == "cuda",
        "ddpm_steps": 32,
        "shape": list(result.shape),
        "finite": True,
        "initialization_ms": (initialized - started) * 1000,
        "prediction_ms": durations[0],
        "warm_median_ms": float(np.median(durations[1:])),
        "warm_p95_ms": float(np.percentile(durations[1:], 95)),
        "first_target_rad": first.tolist(),
        "first_minus_reference_rad": (first-qpos[-1]).tolist(),
    }


def inspect(arch, load=False, root=NEW_ROOT):
    release = new_release(arch, root)
    views, _ = reference_images(release)
    result = release.record()
    result["reference_views"] = list(views)
    result["prediction"] = smoke_prediction(release) if load else None
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arch", choices=("cnn", "transformer"))
    parser.add_argument("--load", action="store_true",
                        help="also load the pinned Route and run one 32-step prediction")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.arch, args.load), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
        parser.exit(1, f"release inspection failed: {exc}\n")
