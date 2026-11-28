"""Reload a pilot checkpoint and inspect a fixed 0.4 s lookahead on the held-out trial."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler

from train import B, H, SEED, Policy


@torch.no_grad()
def plot(data, run, episode=None):
    ckpt = torch.load(run/"pilot.pt", map_location="cuda")
    model = Policy().cuda().eval()
    model.load_state_dict(ckpt["model"])
    heldout = ckpt["config"]["validation"]
    if episode is None:
        eid, = heldout
    else:
        if episode not in heldout:
            raise ValueError("Select an episode from the held-out split")
        eid = episode
    with np.load(data/f"ep{eid}.npz") as z:
        rgb, obs, targets = [torch.from_numpy(z[k]) for k in ("rgb", "obs", "action")]
    sched = DDPMScheduler(num_train_timesteps=32, beta_schedule="squaredcos_cap_v2")
    sched.set_timesteps(32)
    generator = torch.Generator(device="cuda").manual_seed(SEED+1)
    anchors = np.arange(1, len(obs)-H, 2)
    preds = []
    for start in range(0, len(anchors), B):
        cur = anchors[start:start+B]
        rows = torch.from_numpy(np.stack((cur-1, cur), axis=1))
        s = (obs[rows].cuda()-ckpt["obs_center"])/ckpt["obs_scale"]
        cond = model.condition(rgb[rows].cuda(), s)
        v = torch.randn((len(cur), H, 16), device="cuda", generator=generator)
        for t in sched.timesteps:
            noise = model.unet(v, t.to(v.device), global_cond=cond)
            v = sched.step(noise, int(t), v, generator=generator).prev_sample
        preds.extend((v[:,3,-1]*ckpt["action_scale"][-1]+ckpt["action_center"][-1]).cpu().tolist())
    xs = (anchors+4)/10
    fig, ax = plt.subplots(figsize=(9, 3.5))
    ax.plot(xs, targets[anchors+4, -1], label="Recorded target", color="black")
    ax.plot(xs, obs[anchors, -1], label="Hold feedback", color="gray", ls="--")
    ax.plot(xs, preds, label="Diffusion prediction", color="#007d83", alpha=.85)
    ax.set(title=f"Held-out episode {eid}: right gripper, 0.4 s ahead",
           xlabel="Target time (s)", ylabel="Gripper target (native units)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(run/("right_grip.png" if episode is None else f"right_grip_ep{eid}.png"), dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--episode", type=int)
    args = parser.parse_args()
    plot(args.data, args.run, args.episode)
