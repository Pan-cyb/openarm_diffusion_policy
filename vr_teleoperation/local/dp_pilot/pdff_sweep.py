"""Offline bimanual gain/compensation sweep, sampled at 50 Hz."""
import argparse
import json
from pathlib import Path

import mujoco as mj
import numpy as np
import yaml

from pedestal_pdff import BASE, MODEL, ROOT, SIDES, ELBOW_KP, ELBOW_KD, feedforward

CELL = BASE.with_name("openarm_cell_higher_pd.yaml")
FIELD = Path("/media/pan/Expansion1/openarm_datasets/diagnostics/dp_trials/"
             "motion-stop-1795519803309237675/timeline.jsonl")
OUT = ROOT / "reports/2026-11-27-pdff-all"
GROUPS = ("ped0", "ped50", "ped100", "cell0", "cell50", "cell100", "j4old")
AXES = [f"{s}_J{i}" for s in SIDES for i in range(1, 8)]
DT = .020


def gains(group):
    if group not in GROUPS:
        raise ValueError(group)
    src = CELL if group.startswith("cell") else BASE
    cfg = yaml.safe_load(src.read_text())["control_gains"]
    kp, kd = (np.array(cfg[k][:7]) for k in ("kps", "kds"))
    if group == "j4old":
        kp[3], kd[3] = ELBOW_KP, ELBOW_KD
    return np.tile(kp, 2), np.tile(kd, 2)


def ff(group, gravity):
    if group == "j4old":
        result = np.zeros(14)
        result[[3, 10]] = feedforward(gravity[[3, 10]])
        return result
    return gravity * int(group.removeprefix("cell").removeprefix("ped")) / 100.


def replay(path):
    rows = [r for line in path.read_text().splitlines() if (r := json.loads(line))["kind"] == "driver"]
    sides = []
    for s in SIDES:
        side = sorted((r for r in rows if r["side"] == s), key=lambda r: r["dispatch_ns"])
        stamps = np.array([r["dispatch_ns"] for r in side], dtype=np.int64)
        q = np.array([r["q_command"] for r in side])
        if len(q) < 2 or q.shape[1:] != (8,) or not np.isfinite(q).all() or not (np.diff(stamps) > 0).all():
            raise ValueError(f"invalid {s} command series")
        sides.append((stamps, q))
    start = max(t[0] for t, _ in sides)
    stop = min(t[-1] for t, _ in sides)
    period = round(DT * 1e9)
    grid = np.arange(start, stop - period + 1, period, dtype=np.int64)
    if not len(grid):
        raise ValueError("no shared time interval")
    return np.concatenate([q[t.searchsorted(grid, side="right")-1] for t,q in sides], axis=1)


def hold(pose):
    row = yaml.safe_load(BASE.read_text())["start"]["moves"][-1]["position"]
    q = np.array(row["left_arm"] + row["right_arm"])
    if pose == "bent":
        q[[3, 11]] = .65
    return np.tile(q, (150, 1))


def run(targets, group, *, no_contact=False, peak_info=None):
    kp, kd = gains(group)
    m = mj.MjModel.from_xml_path(str(MODEL))
    if no_contact:
        m.opt.disableflags |= mj.mjtDisableBit.mjDSBL_CONTACT
    d, static = mj.MjData(m), mj.MjData(m)
    ctrl, pos, dof = [], [], []
    for side in SIDES:
        for j in range(1, 8):
            joint = m.joint(f"openarm_{side}_joint{j}")
            ctrl.append(m.actuator(f"{side}_joint{j}_ctrl").id)
            pos.append(joint.qposadr.item())
            dof.append(joint.dofadr.item())
    ctrl, pos, dof = map(np.asarray, (ctrl, pos, dof))
    fingers = [m.actuator(f"{s}_finger1_ctrl").id for s in SIDES]
    m.actuator_gainprm[ctrl, 0] = kp
    m.actuator_biasprm[ctrl, 1] = -kp
    m.actuator_biasprm[ctrl, 2] = -kd
    arm = targets[:, [0,1,2,3,4,5,6,8,9,10,11,12,13,14]]
    d.qpos[pos] = arm[0]
    for s, angle in zip(SIDES, targets[0, [7, 15]]):
        for j in (1, 2):
            d.qpos[m.joint(f"openarm_{s}_finger_joint{j}").qposadr.item()] = angle
    n = round(DT/m.opt.timestep)
    assert np.isclose(n*m.opt.timestep, DT)
    errors, efforts, peak = [], [], -1.
    for q, a in zip(targets, arm):
        static.qpos[:] = d.qpos
        # static.qvel stays zero: no Coriolis term in gravity compensation.
        mj.mj_forward(m, static)
        effort = ff(group, static.qfrc_bias[dof])
        m.actuator_biasprm[ctrl, 0] = effort
        d.ctrl[ctrl], d.ctrl[fingers] = a, q[[7, 15]]
        mj.mj_step(m, d, nstep=n)
        errors.append(np.rad2deg(a - d.qpos[pos]))
        efforts.append(effort)
        worst = float(abs(errors[-1]).max())
        if peak_info is not None and worst > peak:
            peak = worst
            peak_info.update(t_s=round(len(errors)*DT, 3), contacts=[
                [m.body(m.geom_bodyid[i]).name for i in c.geom]
                for c in d.contact if c.efc_address >= 0])
    if any(w.number for w in d.warning) or not np.isfinite(errors).all():
        raise RuntimeError(f"{group}: invalid simulation")
    return np.array(errors), np.array(efforts)


def stats(errors):
    rmse = np.sqrt((errors**2).mean(axis=0))
    t,j = np.unravel_index(np.abs(errors).argmax(), errors.shape)
    return dict(rmse_deg=float(np.sqrt((errors**2).mean())),
                per_axis_rmse_deg=rmse.tolist(), worst_rmse_axis=AXES[rmse.argmax()],
                peak_deg=float(abs(errors[t,j])), peak_axis=AXES[j],
                tail_worst_mae_deg=float(np.abs(errors[-25:]).mean(axis=0).max()))


def plot(out, results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": ["Noto Sans CJK JP", "DejaVu Sans"],
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.unicode_minus": False, "font.size": 10})
    colors = ["#256081", "#327da2", "#579bb8", "#94361b", "#c4612d", "#ec8035", "#60676d"]
    labels = ["原 PD / 0%", "原 PD / 50%", "原 PD / 100%",
              "Cell PD / 0%", "Cell PD / 50%", "Cell PD / 100%", "旧版：仅 J4"]
    def lines(ax, scene, component=None):
        for group, color, label, ls in zip(GROUPS, colors, labels, ["--","-.", "-", "--","-.","-",":"]):
            e = results[scene, group]
            ax.plot(DT*np.arange(1,len(e)+1), np.abs(e).max(axis=1) if component is None else e[:,component],
                    ls, color=color, lw=1.3, label=label)
        ax.grid(alpha=.18)
        ax.margins(x=0)
    fig, axs = plt.subplots(3,1,figsize=(10.6,8.6))
    for ax, scene, title in zip(axs, ("home","bent","replay"),
                               ("A   官方启动位保持", "B   屈肘位保持", "C   抓空试验指令回放（无抽屉接触）")):
        lines(ax, scene)
        ax.set(ylabel="最大绝对误差 (°)", xlabel="时间 (s)")
        ax.set_title(title, loc="left", fontsize=11)
    fig.suptitle("双臂 14 轴跟踪误差  |  每个时刻取最差关节", fontsize=16, y=.985)
    fig.legend(*axs[0].get_legend_handles_labels(), loc="upper center", bbox_to_anchor=(.5,.95), ncol=4)
    fig.tight_layout(rect=(0,0,1,.87))
    fig.savefig(out/"overview.png", dpi=145)
    plt.close(fig)
    fig, axs = plt.subplots(7,2,sharex=True,sharey="row",figsize=(13,13))
    for j in range(7):
        for s in range(2):
            ax = axs[j,s]
            lines(ax, "replay", s*7+j)
            ax.set_title(f"{('左','右')[s]} J{j+1}", loc="left", fontsize=10)
            if not s: ax.set_ylabel("误差 (°)")
            if j == 6: ax.set_xlabel("时间 (s)")
    fig.suptitle("抓空试验指令回放  |  指令角减模型反馈角", fontsize=16, y=.99)
    fig.legend(*axs[0,0].get_legend_handles_labels(), loc="upper center", bbox_to_anchor=(.5,.965), ncol=7)
    fig.tight_layout(rect=(0,0,1,.935))
    fig.savefig(out/"per_joint.png", dpi=135)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", type=Path, default=FIELD)
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args()
    ref = {"home": hold("home"), "bent": hold("bent"), "replay": replay(args.timeline)}
    results, arrays, summary = {}, {}, {"axis_order": AXES, "dt_s": DT, "source": str(args.timeline),
                                       "simulation_only": True, "mujoco": mj.__version__, "scenes": {}}
    for scene, targets in ref.items():
        arrays[scene+"_reference_rad"] = targets
        summary["scenes"][scene] = {}
        for g in GROUPS:
            info = {}
            e, torque = run(targets, g, peak_info=info if (scene,g)==("replay","cell100") else None)
            results[scene,g] = arrays[f"{scene}_{g}_err_deg"] = e
            arrays[f"{scene}_{g}_ff_nm"] = torque
            summary["scenes"][scene][g] = stats(e)
            if info:
                summary["peak_contact_snapshot"] = info
    free, _ = run(ref["replay"], "cell100", no_contact=True)
    summary["diagnostic_cell100_contact_off"] = stats(free)
    arrays["diagnostic_cell100_contact_off_err_deg"] = free
    args.out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out/"series.npz", **arrays)
    (args.out/"metrics.json").write_text(json.dumps(summary, indent=2, allow_nan=False)+"\n")
    plot(args.out, results)
    print(args.out)


if __name__ == "__main__":
    main()
