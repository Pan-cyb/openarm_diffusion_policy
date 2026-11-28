"""Offline, nondestructive grasp-phase inputs derived from the existing 10 Hz grid."""
import argparse
import json
from pathlib import Path
import shutil

import cv2
import numpy as np

BASE = Path(__file__).resolve().parents[2]/"outputs/dp_baseline49_20261123"
VIEWS = ("global","chest","wrist_left","wrist_right")


def phase(q, y):
    # Use a conservative change threshold, BOTH hands and leader+follower, keeping
    # two lead-in frames. Never cut through the first observed departure.
    active = (abs(q-q[0]).max(1)>.035) | (abs(y-y[0]).max(1)>.035)
    changed = np.flatnonzero(active)
    trim = max(0,min(12,int(changed[0])-2)) if len(changed) else 0
    closed = q[:,7]<.4
    events = np.flatnonzero(closed[1:] & closed[:-1])
    if not len(events): raise ValueError("missing sustained left close")
    g=int(events[0])
    if trim>=g: raise ValueError("trim would remove grasp")
    return trim,g,min(len(q),g+21)


def prepare(out):
    from prepare import check_cohort
    import yaml
    manifest=json.loads((BASE/"data/manifest.json").read_text())
    assert tuple(manifest["camera_order"])==VIEWS
    root=Path(manifest["source"]); split=manifest["split"]; ids=split["train"]+split["validation"]
    original=check_cohort(root,ids,yaml.safe_load((root/"metadata.yaml").read_text()))
    if out.resolve().is_relative_to(root.resolve()): raise ValueError("refuse raw dataset output")
    if shutil.disk_usage(out.parent).free<4*1024**3: raise OSError("insufficient output space")
    out.mkdir(exist_ok=False)
    cv2.setNumThreads(1)
    info={}
    for i,source in zip(ids,original):
        with np.load(BASE/f"data/ep{i}.npz") as z:
            cut,g,n=phase(z["obs"],z["action"]); grid=z["time_ns"][:n]; low=z["rgb"][:n]
        dst=np.lib.format.open_memmap(out/f"ep{i}.npy",mode="w+",dtype=np.uint8,shape=(n,4,108,192,3))
        for v,view in enumerate(VIEWS):
            files=sorted((root/f"episodes/{i}/cameras/{view}").glob("*.jpeg"),key=lambda f:int(f.stem))
            stamps=np.array([int(f.stem) for f in files])
            idx=stamps.searchsorted(grid,side="right")-1
            if idx.min()<0 or np.max(grid-stamps[idx])>=100_000_000: raise ValueError((i,view,"bad age"))
            for t,j in enumerate(idx):
                im=cv2.imread(str(files[j]))
                if im is None or im.shape!=(720,1280,3): raise ValueError(files[j])
                if not np.array_equal(cv2.resize(im,(96,54),interpolation=cv2.INTER_AREA)[:,:,::-1],low[t,v]):
                    raise ValueError((i,view,t,"baseline pixels differ"))
                dst[t,v]=cv2.resize(im,(192,108),interpolation=cv2.INTER_AREA)[:,:,::-1]
        dst.flush();del dst
        info[i]=dict(source=source,trim=cut,close=g,frames=n)
        print(f"{len(info)}/{len(ids)} ep{i} frames={n}",flush=True)
    (out/"manifest.json").write_text(json.dumps(dict(split=split,episodes=info,views=VIEWS),indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("out",type=Path)
    prepare(p.parse_args().out)
