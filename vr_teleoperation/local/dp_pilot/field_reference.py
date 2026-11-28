"""Immutable, offline-validated targets for the supervised pedestal replay."""
import hashlib
from copy import deepcopy

import mujoco as mj
import numpy as np

from task_reference import DATA
from pedestal_pdff import MODEL

SHA = "79b730440466cc03c20dd0b1c9b75a25e029793f86343919e79ceb9ef4a5e363"
RATE = np.tile([.30]*7+[.50], 2)
DT = .02


def replay_config_type(base):
    """Override only the fixed replay startup target; keep vendor interpolation and J8."""
    class WideHomeConfig(base):
        def get_start_config(self):
            start = deepcopy(super().get_start_config())
            if len(start["moves"]) != 1:
                raise ValueError("unexpected pedestal home sequence")
            pos = start["moves"][0]["position"]
            # Minimum-norm Jacobian IK from nominal home, fixed EE orientation, left +Y 40 mm.
            left = np.array([-.004101849,-.092592090,-.089975489,1.553960515,
                             -.091846450,.004371457,-.091140116])
            for side, angles in (("left_arm", left), ("right_arm", left*[-1,-1,-1,1,-1,-1,-1])):
                if not np.allclose(pos[side][:7], [0,0,0,np.pi/2,0,0,0], atol=1e-6, rtol=0):
                    raise ValueError("upstream home changed; recompute wide home")
                pos[side][:7] = angles.tolist()
                if not np.all((self.get_joint_limits(side)[:7,0] <= angles)
                              & (angles <= self.get_joint_limits(side)[:7,1])):
                    raise ValueError("wide home outside pedestal limits")
            return start
    return WideHomeConfig


def reference(config):
    with np.load(DATA/"ep23.npz", allow_pickle=False) as ep:
        a, t = ep["action"][32:53].astype("<f4"), ep["time_ns"][32:53]
    if (a.shape != (21,16) or hashlib.sha256(a.tobytes()).hexdigest() != SHA
            or not np.all(np.diff(t) == 100_000_000)):
        raise ValueError("episode 23 target/clock mismatch")
    knots = (t-t[0])*2e-9
    path = np.stack([np.interp(np.arange(200)*DT, knots, x) for x in a.T], axis=1)
    path = np.vstack((np.tile(a[0], (150,1)), path, np.tile(a[-1], (150,1))))
    limits = np.vstack([config.get_joint_limits(s+"_arm") for s in ("left","right")])
    if not np.all((path>=limits[:,0]) & (path<=limits[:,1])) or np.any(abs(np.diff(path,axis=0)) > RATE*DT):
        raise ValueError("trajectory exceeds pedestal limits or replay rates")
    home = np.concatenate([config.get_start_config()["moves"][-1]["position"][s+"_arm"]
                           for s in ("left","right")])
    return path, home


def screen(rows):
    m = mj.MjModel.from_xml_path(str(MODEL))
    d = mj.MjData(m)
    ids, columns = [], []
    for i,s in enumerate(("left","right")):
        for k,name in enumerate([f"joint{j}" for j in range(1,8)]+["finger_joint1","finger_joint2"]):
            ids.append(m.joint(f"openarm_{s}_{name}").qposadr.item())
            columns.append(i*8+min(k,7))
    for i,row in enumerate(rows):
        if not np.isfinite(row).all():
            raise ValueError("nonfinite trajectory")
        d.qpos[ids] = row[columns]
        mj.mj_forward(m,d)
        for c in d.contact:
            if c.dist < 0:
                names = [m.body(m.geom_bodyid[g]).name for g in c.geom]
                raise ValueError(f"model penetration at sample {i}: {names}, {c.dist*1000:.2f} mm")


def screen_reference(path, home):
    bound = np.arange(1+int(np.ceil(np.max(abs(path[0]-home)/RATE)/DT)))[:,None]*DT*RATE
    screen(np.vstack((home+np.clip(path[0]-home, -bound, bound), path)))
