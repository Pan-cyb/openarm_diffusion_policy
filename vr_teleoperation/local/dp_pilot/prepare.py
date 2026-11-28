"""Prepare the three-episode, 10 Hz offline drawer pilot (capture Python env)."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pyarrow.parquet as pq
import yaml


CAMS = ("global", "chest", "wrist_left", "wrist_right")
IDS = (0, 26, 33)
PERIOD = 100_000_000


def check_cohort(root, ids, meta):
    """Match merged IDs back to the actual sources before applying exclusions."""
    contract = meta["capture_contract"]
    assert contract["collection_mode"] == "real" and not contract["synthetic_arms"] and not contract["mock"]
    mapping = json.loads((root/"episode_mapping.json").read_text())["episodes"]
    policy = json.loads((root.parent.parent/"training_exclusions.json").read_text())
    cutoff = policy["cutoff_inclusive"]
    mapping = {int(r["merged_episode_id"]): (r["source_session"], int(r["source_episode_id"]))
               for r in mapping}
    selected = [mapping[i] for i in ids]
    assert len(set(selected)) == len(ids)
    for session, original in selected:
        if (session, original) <= (cutoff["session"], int(cutoff["episode_id"])):
            raise ValueError(f"Historical exclusion: {session}, {original}")
        for rule in policy["entries"]:
            if rule["session"] == session and (rule["exclude_entire_session"] or
                                               str(original) in rule.get("excluded_episode_ids", [])):
                raise ValueError(f"Explicit exclusion: {session}, {original}")
    return selected


def prepare(root, out, eid):
    ep = root / "episodes" / str(eid)
    clocks, frames, joints = {}, {}, {}
    for cam in CAMS:
        files = sorted((ep / "cameras" / cam).glob("*.jpeg"), key=lambda p: int(p.stem))
        clocks[cam] = np.array([int(p.stem) for p in files], dtype=np.int64)
        frames[cam] = files
    for kind in ("obs", "action"):
        for side in ("left", "right"):
            key = f"{kind}/{side}"
            table = pq.read_table(ep / kind / "arms" / side / "state.parquet",
                                  columns=["timestamp", "qpos"])
            clocks[key] = table["timestamp"].cast("int64").to_numpy()
            values = np.array(table["qpos"].to_pylist(), dtype=np.float32)
            assert values.shape == (len(clocks[key]), 8) and np.isfinite(values).all()
            joints[key] = values
    assert all(len(ts) and np.all(np.diff(ts) > 0) for ts in clocks.values())
    grid = np.arange(max(ts[0] for ts in clocks.values()),
                     min(ts[-1] for ts in clocks.values()) + 1, PERIOD, dtype=np.int64)
    age_ms, indices = {}, {}
    for key, ts in clocks.items():
        idx = ts.searchsorted(grid, side="right") - 1
        age = (grid - ts[idx]) / 1e6
        assert len(grid) >= 10 and min(idx) >= 0 and min(age) >= 0
        if max(age) >= PERIOD / 1e6:
            raise ValueError(f"episode {eid}: stale {key}, max age {max(age):.1f} ms")
        age_ms[key] = round(float(max(age)), 2)
        indices[key] = idx
    arrays = {k: np.concatenate([joints[key][indices[key]] for key in (k+"/left", k+"/right")],
                                axis=1) for k in ("obs", "action")}
    rgb = np.empty((len(grid), 4, 54, 96, 3), dtype=np.uint8)
    for c, cam in enumerate(CAMS):
        for t, i in enumerate(indices[cam]):
            img = cv2.imread(str(frames[cam][i]), cv2.IMREAD_COLOR)
            if img is None or img.shape[:2] != (720, 1280):
                raise ValueError(str(frames[cam][i]))
            rgb[t, c] = cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA)[:, :, ::-1]
    np.savez(out / f"ep{eid}.npz", time_ns=grid, rgb=rgb, **arrays)
    # Show start/end and the strongest right-gripper changes, plus even time samples.
    idxs = np.unique(np.r_[np.linspace(0, len(grid)-1, 6).astype(int),
                           np.argmax(np.diff(arrays["action"][:, -1]))+1,
                           np.argmin(np.diff(arrays["action"][:, -1]))+1])
    rows = []
    for i in idxs:
        row = np.ascontiguousarray(np.hstack(rgb[i])[:, :, ::-1])
        label = f'{i/10:.1f}s  R grip obs={arrays["obs"][i,-1]:.2f} target={arrays["action"][i,-1]:.2f}'
        row = cv2.copyMakeBorder(row, 17, 0, 0, 0, cv2.BORDER_CONSTANT)
        cv2.putText(row, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, .38, (255, 255, 255), 1)
        rows.append(row)
    assert cv2.imwrite(str(out / f"preview{eid}.jpg"), np.vstack(rows))
    return {"steps": len(grid), "max_age_ms": age_ms}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--all49", action="store_true")
    args = parser.parse_args()
    meta = yaml.safe_load((args.root / "metadata.yaml").read_text())
    ids = tuple(range(49)) if args.all49 else IDS
    assert set(ids) <= {int(e["id"]) for e in meta["episodes"] if e["success"] is True}
    check_cohort(args.root, ids, meta)
    assert not args.out.resolve().is_relative_to(args.root.resolve())
    args.out.mkdir(parents=True, exist_ok=False)
    cv2.setNumThreads(1)
    info = {"source": str(args.root.resolve()), "camera_order": CAMS, "hz": 10,
            "image": "RGB, native stored orientation, area resize to 96x54",
            "arms": "left then right, 8 qpos each; action is leader/IK target",
            "episodes": {}}
    info["rejected"] = {}
    for eid in ids:
        try:
            info["episodes"][eid] = prepare(args.root, args.out, eid)
        except (AssertionError, ValueError) as exc:
            if not args.all49:
                raise
            info["rejected"][eid] = repr(exc)
            print(eid, "rejected", repr(exc), flush=True)
            continue
        print(eid, info["episodes"][eid], flush=True)
    if args.all49:
        held_out = {7, 19, 29, 37, 45}
        info["split"] = {"train": sorted(set(info["episodes"]) - held_out),
                         "validation": sorted(set(info["episodes"]) & held_out)}
    (args.out / "manifest.json").write_text(json.dumps(info, indent=2))
