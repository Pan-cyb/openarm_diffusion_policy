"""Export a completed baseline's console loss samples and measured validation metrics."""
import argparse
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from torch.utils.tensorboard import SummaryWriter


def export(run, lines):
    metrics = json.loads((run / "metrics.json").read_text())
    steps = metrics["steps"]
    found = {}
    for line in lines:
        m = re.fullmatch(r"(\d+) (\d+\.\d+)", line.strip())
        if m:
            i, loss = int(m[1]), float(m[2])
            assert 0 < i <= steps and i % 100 == 0 and i not in found
            found[i] = loss
    assert found and max(found) == steps
    out = run / "dashboard"
    out.mkdir(exist_ok=False)
    axis = list(range(100, steps + 1, 100))
    vals = [found.get(i, np.nan) for i in axis]
    stages = [(0, metrics["before"]["validation"]),
              (metrics["matched_steps"]["steps"], metrics["matched_steps"]["validation"]),
              (steps, metrics["after"]["validation"])]
    with SummaryWriter(str(out/"events"/"44_demos")) as w:
        for i, loss in sorted(found.items()):
            w.add_scalar("train/noise_mse_mean_100_steps", loss, i)
        for i, item in stages:
            for k in ("normalized_mae", "right_grip_changing_mae_native"):
                w.add_scalar("validation/"+k, item[k], i)
    with SummaryWriter(str(out/"events"/"hold_position")) as w:
        for i, item in stages:
            w.add_scalar("validation/normalized_mae", item["hold_feedback_normalized_mae"], i)
    (out/"loss_samples.json").write_text(json.dumps({
        "description": "Training console, mean per 100 updates; missing samples not estimated.",
        "missing_steps": sorted(set(axis)-found.keys()), "points": sorted(found.items())}, indent=2))
    plt.rcParams.update({"font.family": "Noto Sans CJK JP", "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, (a,b) = plt.subplots(1,2,figsize=(10.5,3.8), constrained_layout=True)
    a.plot(axis, vals, ".-", color="#077965", ms=3)
    a.set(yscale="log", xlabel="训练步数", ylabel="噪声预测 MSE（对数刻度）",
          title="训练 loss｜每 100 步的均值")
    a.set_yticks([.03,.05,.1,.3,1], ["0.03","0.05","0.1","0.3","1"])
    names = ["保持位置", "2 条示范\n3,000 步", "44 条示范\n3,000 步", "44 条示范\n10,000 步"]
    err = [stages[-1][1]["hold_feedback_normalized_mae"],
           metrics["reference_validation"]["normalized_mae"],
           stages[1][1]["normalized_mae"], stages[2][1]["normalized_mae"]]
    bars = b.bar(names, err, color=["#777b82","#bec4cd","#77b8a9","#077965"], width=.63)
    b.bar_label(bars, fmt="%.4f", padding=3)
    b.set(ylim=(0,.20), ylabel="归一化动作 MAE", title="验证误差｜同样的 5 条留出轨迹")
    for ax in (a,b):
        ax.set_axisbelow(True)
        ax.grid(axis="y", alpha=.18)
    fig.savefig(out/"training.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    export(p.parse_args().run, sys.stdin)
