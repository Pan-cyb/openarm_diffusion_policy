"""Compute the previous study's first-target Cartesian comparison on the new split."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mujoco as mj
import numpy as np


def compare(base):
    model = mj.MjModel.from_xml_path(str(Path(__file__).resolve().parents[3] /
        "openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"))
    data = mj.MjData(model)
    sides = ("left", "right")
    addr = [model.joint(f"openarm_{s}_joint{i}").qposadr.item() for s in sides for i in range(1, 8)]
    sites = [model.site(s+"_ee_control_point").id for s in sides]
    def fk(rows):
        result = np.empty((len(rows),2,3))
        for idx, q in enumerate(rows):
            data.qpos[addr] = np.r_[q[:7], q[8:15]]
            mj.mj_kinematics(model, data)
            result[idx] = data.site_xpos[sites]
        return result
    paths, stats = {}, {}
    for arch in ("cnn", "transformer"):
        with np.load(base/arch/"eval.npz") as z:
            anchors = z["ep_t"]
            truth = fk(z["truth"][:,0])
            hold = fk(z["hold"][:,0])
            xyz = np.stack([fk(draw[:,0]) for draw in z["prediction"]])
        distance = 100*np.linalg.norm(xyz-truth, axis=-1)
        stats[arch] = distance.mean((0,1)).tolist()
        stats["hold"] = (100*np.linalg.norm(hold-truth,axis=-1)).mean(0).tolist()
        mask = anchors[:,0] == anchors[0,0]
        paths[arch] = xyz[0,mask]
        paths["recorded target"] = truth[mask]
    fig, axes = plt.subplots(1,2,figsize=(9,4),constrained_layout=True)
    for s, axis in enumerate(axes):
        for name, color in (("recorded target","black"),("cnn","#b65026"),("transformer","#187781")):
            p = paths[name][:,s,:]*100
            axis.plot(p[:,0],p[:,2],label=name,color=color,alpha=.8,lw=1.3)
        axis.set(title=sides[s]+" control point",xlabel="model X (cm)",ylabel="model Z (cm)",aspect="equal")
        axis.legend(fontsize=8)
    fig.suptitle(f"Validation episode {anchors[0,0]} | first targets at recorded observations, not rollout")
    fig.savefig(base/"validation_paths.png",dpi=140)
    plt.close(fig)
    return stats
