"""Offline parity check against raw JPEG/Parquet (use the capture Python env)."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pyarrow.parquet as pq

from shadow_inputs import PERIOD_NS, VIEWS, pack_snapshot


def check(data):
    m = json.loads((data/"manifest.json").read_text())
    checked = 0
    for eid in m["split"]["validation"]:
        ep = Path(m["source"])/"episodes"/str(eid)
        views = {k: sorted(int(p.stem) for p in (ep/"cameras"/k).glob("*.jpeg")) for k in VIEWS}
        tables = {k: pq.read_table(ep/"obs/arms"/k/"state.parquet", columns=["timestamp", "qpos"])
                  for k in ("left", "right")}
        with np.load(data/f"ep{eid}.npz") as z:
            grid, rgb, obs = (z[k] for k in ("time_ns", "rgb", "obs"))
        for i in (0, len(grid)//2, len(grid)-1):
            t = grid[i]
            cams, arms = {}, {}
            for k, ts in views.items():
                stamp = ts[np.searchsorted(ts, t, side="right")-1]
                cams[k] = stamp, cv2.imread(str(ep/"cameras"/k/f"{stamp}.jpeg"))
            for side, tab in tables.items():
                ts = tab["timestamp"].cast("int64").to_numpy()
                j = ts.searchsorted(t, side="right")-1
                arms[side] = ts[j], np.asarray(tab["qpos"][int(j)].as_py(), dtype=np.float32)
            x, s = pack_snapshot(t, cams, arms)
            np.testing.assert_array_equal(x, rgb[i])
            np.testing.assert_array_equal(s, obs[i])
            checked += 1
    for c, a in [(dict(list(cams.items())[1:]), arms),
                 ({**cams, "global": (t-PERIOD_NS, cams["global"][1])}, arms),
                 (cams, {**arms, "left": (t+1, arms["left"][1])}),
                 (cams, {**arms, "right": (t, np.full(8, np.nan, np.float32))})]:
        try:
            pack_snapshot(t, c, a)
        except ValueError:
            continue
        raise AssertionError("invalid snapshot accepted")
    print(f"PASS: {checked} raw snapshots equal training RGB/qpos; 4 invalid cases rejected")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("data", type=Path)
    check(p.parse_args().data)
