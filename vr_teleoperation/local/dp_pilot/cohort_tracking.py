"""Time-align dispatched targets with subsequent observations in selected recordings."""
import json
from pathlib import Path

import numpy as np


def tracking(base):
    manifest = json.loads((base/"data/manifest.json").read_text())
    source = Path(manifest["source"])
    bounds = {}
    for episode in manifest["episodes"]:
        with (source/f"camera_timing/episode_{episode}.jsonl").open("rb") as f:
            begin = json.loads(f.readline())["command_metadata"]["episode_start_timestamp_ns"]
            f.seek(-1024, 2)
            tail = f.read().splitlines()[-2:]
        end = json.loads(tail[0])["episode_end_timestamp_ns"]
        bounds[episode] = begin, end
    out = {}
    traces = source.parent/".capture-config"/source.name
    for side in ("left", "right"):
        measured, efforts = [], []
        by_ep = {}
        for path in sorted(traces.glob(f"gravity-{side}-*.jsonl")):
            with path.open() as f:
                rows = [json.loads(line) for line in f]
            if not rows:
                continue
            dispatch = np.array([r["dispatch_ns"] for r in rows])
            ts = np.array([r["feedback_ns"] for r in rows])
            if not np.all(np.diff(dispatch) > 0):
                raise ValueError(f"Nonmonotonic dispatch in {path}")
            cmd = np.array([r["q_command"][:7] for r in rows])
            obs = np.array([r["q_obs"][:7] for r in rows])
            tau = np.array([r["tau_ff_nm"] for r in rows])
            prev = np.searchsorted(dispatch, ts, side="right") - 1
            for ep, (begin, end) in bounds.items():
                use = (prev>=0) & (dispatch[prev]>=begin) & (ts<=end) & (ts-dispatch[prev]<100_000_000)
                if np.any(use):
                    measured.append(np.rad2deg(cmd[prev[use]] - obs[use]))
                    efforts.append(tau[prev[use],3])
                    by_ep[ep] = by_ep.get(ep,0) + int(use.sum())
        if set(by_ep) != set(bounds):
            raise ValueError(f"{side}: missing in-episode paired dispatch evidence")
        e = np.concatenate(measured)
        out[side] = {"matched_rows_by_episode":by_ep,
                     "j1_to_j7_absolute_error_median_deg":np.median(abs(e),axis=0).tolist(),
                     "j1_to_j7_absolute_error_p95_deg":np.percentile(abs(e),95,axis=0).tolist(),
                     "j4_requested_ff_median_Nm":float(np.median(np.concatenate(efforts)))}
    with (base/"capture_tracking.json").open("x") as f:
        json.dump(out, f, indent=2)
    return out
