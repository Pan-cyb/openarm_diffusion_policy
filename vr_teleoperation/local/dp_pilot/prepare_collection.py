"""Offline preparation of one native, committed gravity-compensated capture session."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import yaml

from prepare import CAMS, prepare

LOCAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LOCAL / "four_camera"))
from audit_real_dataset import inspect_episode
from storage import check_storage
from field_reference import screen
from openarm_driver import Config


def dump(path, obj):
    with path.open("x") as f:
        json.dump(obj, f, indent=2, allow_nan=False, ensure_ascii=False)


def select_reference(data, manifest):
    """Choose the earliest usable two-frame anchor from a training trajectory."""
    cfg = Config("openarm_pedestal")
    sides = ("left_arm", "right_arm")
    home = np.concatenate([cfg.get_start_config()["moves"][-1]["position"][s] for s in sides])
    bounds = np.vstack([cfg.get_joint_limits(s) for s in sides])
    frame = 1
    rows = []
    for ep in manifest["split"]["train"]:
        with np.load(data/f"ep{ep}.npz") as z:
            rows.append((ep, z["obs"][frame], z["action"][frame], int(z["time_ns"][frame])))
    points = np.stack([v[1] for v in rows])
    axes = [*range(7), *range(8,15)]
    ranked = sorted(rows, key=lambda r: np.mean(np.abs(points[:, axes]-r[1][axes])))
    rejected = {}
    for ep, q, a, ns in ranked:
        if not np.all((q >= bounds[:, 0]) & (q <= bounds[:, 1])):
            rejected[ep] = "feedback outside official joint limits"
            continue
        # Staged workbench approaches this observation from its official arm preset.
        # Screen the actual bounded joint-rate path, plus inter-arm timing variations.
        delta = q-home
        rate = np.tile([.30]*7+[.50], 2)
        t = np.linspace(0, np.max(abs(delta)/rate), 101)[:,None]
        path = home+np.clip(delta, -rate*t, rate*t)
        try:
            screen(np.vstack([path, [np.r_[l[:8], r[8:]] for l in path[::10] for r in path[::10]]]))
        except ValueError as exc:
            rejected[ep] = str(exc)
            continue
        spec = dict(episode=ep, frame=frame, field="obs", stamp_ns=ns, pose=q.tolist(),
                    pose_sha256=hashlib.sha256(q.astype("<f4").tobytes()).hexdigest(),
                    action_minus_obs_rad=(a-q).tolist(), rejected_candidates=rejected,
                    selection="first full two-observation anchor; minimum mean 14-joint L1 to train starts; checked model path")
        tiles = []
        with np.load(data/f"ep{ep}.npz") as z:
            for col, view in enumerate(CAMS):
                files = sorted((Path(manifest["source"])/f"episodes/{ep}/cameras/{view}").glob("*.jpeg"),
                               key=lambda p: int(p.stem))
                j = np.searchsorted([int(p.stem) for p in files], ns, side="right")-1
                assert j >= 0 and 0 <= ns-int(files[j].stem) < 100_000_000
                image = cv2.imread(str(files[j]))
                assert np.array_equal(cv2.resize(image,(96,54),interpolation=cv2.INTER_AREA)[:,:,::-1],
                                      z["rgb"][frame,col])
                tile = cv2.resize(image, (480,270), interpolation=cv2.INTER_AREA)
                cv2.putText(tile, f"{view}    ep{ep}:{frame}", (12,28), cv2.FONT_HERSHEY_SIMPLEX,
                            .72, (0,255,255), 2, cv2.LINE_AA)
                tiles.append(tile)
        assert cv2.imwrite(str(data.parent/"reference_views.jpg"),
                           np.vstack((np.hstack(tiles[:2]),np.hstack(tiles[2:]))))
        dump(data.parent/"reference.json", spec)
        return spec
    raise ValueError(f"No training start passed checks: {rejected}")


def run(source, base):
    source = source.resolve()
    check_storage(source, min_free_bytes=0)
    meta = yaml.safe_load((source/"metadata.yaml").read_text())
    c = meta["capture_contract"]
    if not (c["collection_mode"] == "real" and not c["synthetic_arms"] and not c["mock"]
            and c["leader_mapping"] == "native-hand-delta-v1"
            and c["start_profile"] == "wide-40mm-direct-v2"
            and c["arm_gravity"]["profile"] == "pedestal-original-pd-first-frame-v2"
            and c["arm_gravity"]["max_percent"] == 100 and c["arm_gravity"]["ramp_seconds"] == 0):
        raise ValueError("Unexpected capture/control contract")
    policy = json.loads((source.parent.parent/"training_exclusions.json").read_text())
    if base.resolve().is_relative_to(source.parent):
        raise ValueError("Never write training artifacts under raw")
    data, audit = base/"data", base/"audit"
    data.mkdir(parents=True, exist_ok=False)
    audit.mkdir(exist_ok=False)
    info = dict(source=str(source), hz=10, camera_order=list(CAMS), capture_contract=c,
                image="RGB 96x54; stored orientation, no second rotation",
                arms="left then right 8 each; obs feedback, action IK target", episodes={}, rejected={})
    traces = source.parent/".capture-config"/source.name
    for side in ("left", "right"):
        count = 0
        for logfile in sorted(traces.glob(f"gravity-{side}-*.jsonl")):
            with logfile.open() as lines:
                for line in lines:
                    sample = json.loads(line)
                    tau = np.asarray(sample["tau_ff_nm"])
                    if sample["scale"] != 1. or tau.shape != (7,) or not np.all(
                            np.abs(tau) <= np.asarray(c["arm_gravity"]["ff_cap_nm"])):
                        raise ValueError(f"Unexpected dispatched gravity: {logfile}")
                    count += 1
        if not count:
            raise ValueError(f"Missing {side} dispatched gravity evidence")
        info.setdefault("gravity_dispatch_rows", {})[side] = count
    cv2.setNumThreads(1)
    for item in meta["episodes"]:
        i = int(item["id"])
        cutoff = policy["cutoff_inclusive"]
        excluded = (source.name, i) <= (cutoff["session"], int(cutoff["episode_id"])) or any(
            x["session"] == source.name and (x["exclude_entire_session"] or str(i) in x.get("excluded_episode_ids", []))
            for x in policy["entries"])
        if item["success"] is not True or excluded:
            info["rejected"][i] = "capture marked failed" if not item["success"] else "training exclusion"
            continue
        if not (source/"episodes"/str(i)).is_dir():
            info["rejected"][i] = "metadata success but episode directory absent"
            continue
        try:
            # Validate the same packet hashes and episode boundaries as the field audit.
            report = inspect_episode(source, str(i), audit, frame_pause_s=0.)
            dump(audit/f"ep{i}.json", report)
            if report["warnings"] or any(v["near_black_frames"] for v in report["cameras"].values()):
                raise ValueError("frozen feedback or near-black frame; inspect per-episode report")
            info["episodes"][i] = prepare(source, data, i)
        except (AssertionError, ValueError, OSError) as exc:
            info["rejected"][i] = f"{type(exc).__name__}: {exc}"
        print("episode", i, info["episodes"].get(i, info["rejected"].get(i)), flush=True)
    ids = sorted(info["episodes"])
    if len(ids) < 5:
        dump(base/"rejected.json", info)
        raise ValueError("Too few complete episodes")
    # Every fifth accepted trajectory, fixed before any model fitting or scoring.
    val = ids[4::5]
    info["split"] = {"train": [i for i in ids if i not in val], "validation": val}
    dump(data/"manifest.json", info)
    print("split", info["split"], "reference", select_reference(data, info), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("base", type=Path)
    args = ap.parse_args()
    run(args.source, args.base)
