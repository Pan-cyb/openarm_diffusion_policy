"""Reproduce the fixed episode-47 gravity comparison. Offline only."""
import hashlib
import json

import numpy as np

from pdff_sweep import DT, run, stats
from task_reference import DATA

ACTION_SHA = "f9f89c083d8bdf81ed908f0a32a99527e1358052b8effa9ba925ba630fee9f9a"


def reference():
    with np.load(DATA / "ep47.npz", allow_pickle=False) as episode:
        a = episode["action"][32:53].astype("<f4")
        t = episode["time_ns"][32:53]
    if (a.shape != (21, 16) or hashlib.sha256(a.tobytes()).hexdigest() != ACTION_SHA
            or not np.all(np.diff(t) == 100_000_000)):
        raise ValueError("unexpected demonstration targets or clock")
    # Half speed; no loop-back or splice to an unrelated reference.
    grid = np.arange(200)*DT
    knots = (t-t[0])*2e-9
    movement = np.stack([np.interp(grid, knots, axis) for axis in a.T], axis=1)
    return np.vstack((np.tile(a[0], (150,1)), movement, np.tile(a[-1], (150,1))))


if __name__ == "__main__":
    ref = reference()
    report = {}
    for group in ("ped0", "ped50", "ped100"):
        e, _ = run(ref, group)
        report[group] = dict(motion_rmse_deg=stats(e[150:350])["rmse_deg"],
                             last_half_second_worst_axis_mae_deg=stats(e)["tail_worst_mae_deg"])
    print(json.dumps(dict(simulation_only=True, episode=47, frames_inclusive=[32,52],
                          action_sha256=ACTION_SHA, metrics=report), indent=2, allow_nan=False))
