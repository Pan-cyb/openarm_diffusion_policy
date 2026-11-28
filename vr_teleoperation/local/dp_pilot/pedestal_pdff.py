"""Offline V2 pedestal J4 hold comparison. No driver, CAN, or camera imports."""
import argparse
import json
from pathlib import Path

import mujoco as mj
import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[3]
MODEL = ROOT / "openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml"
BASE = (ROOT / "vr_teleoperation/upstream/dora-openarm-data-collection/lib/"
        "openarm_driver/src/openarm_driver/configs/openarm_pedestal.yaml")
SIDES = ("left", "right")
PROFILES = ("baseline", "pd", "gravity", "combined")
# Experimental joint-space values, not motor units or a field-safe envelope.
ELBOW_KP, ELBOW_KD = 80., 2.05
FF_FRACTION, FF_CAP_NM = .50, 2.


def feedforward(gravity_nm):
    gravity_nm = np.asarray(gravity_nm, dtype=float)
    if gravity_nm.shape != (2,) or not np.all(np.isfinite(gravity_nm)):
        raise ValueError("expected finite left/right J4 gravity")
    return np.clip(FF_FRACTION * gravity_nm, -FF_CAP_NM, FF_CAP_NM)


class Hold:
    def __init__(self, profile, pose):
        if profile not in PROFILES or pose not in ("home", "bent"):
            raise ValueError("unknown profile or pose")
        self.profile = profile
        self.model = m = mj.MjModel.from_xml_path(str(MODEL))
        self.data = d = mj.MjData(m)
        self.static = mj.MjData(m)
        cfg = yaml.safe_load(BASE.read_text())
        gains = cfg["control_gains"]
        elbows, dofs, coords = [], [], []
        mj.mj_resetDataKeyframe(m, d, m.key("home").id)
        for side in SIDES:
            target = cfg["start"]["moves"][-1]["position"][side + "_arm"].copy()
            if pose == "bent":  # Second model-only pose, not a proposed physical target.
                target[3] = .65
            for j in range(1, 8):
                a = m.actuator(f"{side}_joint{j}_ctrl").id
                joint = m.joint(f"openarm_{side}_joint{j}")
                kp, kd = gains["kps"][j-1], gains["kds"][j-1]
                if j == 4:
                    elbows.append(a)
                    dofs.append(joint.dofadr.item())
                    coords.append(joint.qposadr.item())
                    if profile in ("pd", "combined"):
                        kp, kd = ELBOW_KP, ELBOW_KD
                m.actuator_gainprm[a, 0] = kp
                m.actuator_biasprm[a, :3] = (0., -kp, -kd)
                d.ctrl[a] = d.qpos[joint.qposadr.item()] = target[j-1]
            # Fingers use the original model actuator in all four comparisons.
            d.ctrl[m.actuator(f"{side}_finger1_ctrl").id] = target[7]
            for j in (1, 2):
                d.qpos[m.joint(f"openarm_{side}_finger_joint{j}").qposadr.item()] = target[7]
        self.elbows, self.dofs, self.coords = map(np.asarray, (elbows, dofs, coords))
        mj.mj_forward(m, d)

    def gravity(self):
        # Evaluate bias at the measured configuration with velocity zero,
        # never at the target or with the moving plant's Coriolis term.
        self.static.qpos[:] = self.data.qpos
        self.static.qvel[:] = 0.
        mj.mj_forward(self.model, self.static)
        return self.static.qfrc_bias[self.dofs].copy()

    def run(self):
        m, d = self.model, self.data
        stride = round(.020 / m.opt.timestep)
        if not np.isclose(stride * m.opt.timestep, .020):
            raise ValueError("model timestep incompatible with 50 Hz update")
        tail, peak_ff = [], np.zeros(2)
        for tick in range(150):  # 3 s hold; gravity sampled at 50 Hz.
            ff = (feedforward(self.gravity()) if self.profile in ("gravity", "combined")
                  else np.zeros(2))
            # Add inside the affine position actuator, before its force limits.
            m.actuator_biasprm[self.elbows, 0] = ff
            peak_ff = np.maximum(peak_ff, abs(ff))
            mj.mj_step(m, d, nstep=stride)
            if tick >= 125:
                tail.append(np.rad2deg(d.ctrl[self.elbows] - d.qpos[self.coords]))
        if not np.isfinite(tail).all() or any(w.number for w in d.warning):
            raise RuntimeError("invalid MuJoCo hold")
        return {"mean_signed_error_deg_last_0.5s": np.mean(tail, axis=0).tolist(),
                "peak_abs_ff_nm": peak_ff.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pose", choices=("home", "bent"), default="home")
    args = parser.parse_args()
    print(json.dumps({"simulation_only": True, "pose": args.pose, "axis_order": ["left_J4", "right_J4"],
                      "profiles": {p: Hold(p, args.pose).run() for p in PROFILES}},
                     indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
