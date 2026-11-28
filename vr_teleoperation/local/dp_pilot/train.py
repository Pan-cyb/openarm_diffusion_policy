"""Offline-only visual DDPM pilot; run with the pinned diffusion_policy on PYTHONPATH."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler
from diffusion_policy.model.diffusion.conditional_unet1d import ConditionalUnet1D
from torch import nn


TRAIN, VALID = (0, 26), (33,)
H, B, SEED = 8, 16, 723


class Policy(nn.Module):
    def __init__(self):
        super().__init__()
        self.vision = nn.Sequential(
            nn.Conv2d(3, 16, 5, 3), nn.GroupNorm(4, 16), nn.SiLU(),
            nn.Conv2d(16, 32, 3, 2), nn.GroupNorm(4, 32), nn.SiLU(),
            nn.Conv2d(32, 32, 3, 2), nn.SiLU(), nn.AdaptiveAvgPool2d((2, 3)),
            nn.Flatten(), nn.Linear(192, 24), nn.SiLU())
        self.unet = ConditionalUnet1D(16, global_cond_dim=2*(4*24+16),
                                      diffusion_step_embed_dim=64, down_dims=(64, 128),
                                      cond_predict_scale=True)

    def condition(self, rgb, state):
        b = len(rgb)
        frames = rgb.reshape(-1, 54, 96, 3).permute(0, 3, 1, 2).float()/255
        return torch.cat([self.vision(frames).reshape(b, 2, -1), state], -1).flatten(1)


def main(data, out, steps, reference=None):
    assert steps > 0
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    torch.set_num_threads(2)
    manifest = json.loads((data / "manifest.json").read_text())
    assert manifest["hz"] == 10 and manifest["camera_order"] == ["global", "chest", "wrist_left", "wrist_right"]
    split = manifest.get("split", {"train": TRAIN, "validation": VALID})
    train_ids, val_ids = tuple(split["train"]), tuple(split["validation"])
    assert train_ids and val_ids and not set(train_ids).intersection(val_ids)
    assert len(set(train_ids + val_ids)) == len(train_ids + val_ids)
    assert set(train_ids + val_ids) == {int(i) for i in manifest["episodes"]}
    out.mkdir(parents=True, exist_ok=False)
    e = {}
    for i in train_ids + val_ids:
        with np.load(data / f"ep{i}.npz") as z:
            assert np.all(np.diff(z["time_ns"]) == 100_000_000)
            e[i] = {k: torch.from_numpy(z[k]) for k in ("rgb", "obs", "action")}
    def scale(k):
        x = torch.cat([e[i][k] for i in train_ids]).cuda()
        lo, hi = x.amin(0), x.amax(0)
        return (hi+lo)/2, ((hi-lo)/2).clamp_min(.01)
    sc, ss = scale("obs")
    ac, ass = scale("action")
    def windows(ids):
        return [(i,t) for i in ids for t in range(1, len(e[i]["obs"])-H)]
    def batch(pairs):
        x, s, a = (torch.stack([e[i][k][t-1:t+1] if k != "action" else
                                e[i][k][t+1:t+1+H] for i, t in pairs]).cuda()
                   for k in ("rgb", "obs", "action"))
        return x, (s-sc)/ss, (a-ac)/ass

    train = windows(train_ids)
    policy = Policy().cuda()
    opt = torch.optim.AdamW(policy.parameters(), lr=0.0005)
    sched = DDPMScheduler(num_train_timesteps=32, beta_schedule="squaredcos_cap_v2",
                          prediction_type="epsilon", clip_sample=True)
    @torch.no_grad()
    def evaluate(ids, model=None, norms=None):
        # Same evenly spaced windows and same sampling noise before and after training.
        g = torch.Generator(device="cuda").manual_seed(SEED+1)
        model = policy if model is None else model
        errors, persistence, grp, truth, episodes = [], [], [], [], []
        for i in ids:
            ws = windows((i,))
            pairs = [ws[j] for j in np.linspace(0, len(ws)-1, 48, dtype=int)]
            g.manual_seed(SEED+1+i if "split" in manifest else SEED+1)
            for offset in range(0, len(pairs), B):
                x, s, target = batch(pairs[offset:offset+B])
                state = s if norms is None else (s*ss+sc-norms["obs_center"])/norms["obs_scale"]
                cond = model.condition(x, state)
                v = torch.randn(target.shape, generator=g, device="cuda")
                sched.set_timesteps(32)
                for t in sched.timesteps:
                    noise = model.unet(v, t.to(v.device), global_cond=cond)
                    v = sched.step(noise, int(t), v, generator=g).prev_sample
                if norms is not None:
                    v = (v*norms["action_scale"]+norms["action_center"]-ac)/ass
                errors.append((v-target).abs())
                hold = ((s[:, -1]*ss+sc)-ac)/ass
                persistence.append((hold[:,None,:]-target).abs())
                grp.append(((v-target)*ass)[:,:,-1].abs())
                truth.append((target*ass+ac)[:,:,-1])
                episodes.extend([i]*len(target))
        errors, persistence, grp, truth = map(torch.cat, (errors, persistence, grp, truth))
        moving = (truth.amax(1)-truth.amin(1)) > .1*2*ass[-1]
        episode_ids = torch.tensor(episodes, device=errors.device)
        def directional(mask):
            if not mask.any():
                return {"windows": 0}
            return {"windows": int(mask.sum()), "mae_native": grp[mask].mean().item(),
                    "hold_mae_native": (persistence[mask,:,-1]*ass[-1]).mean().item()}
        return {"windows": len(errors), "normalized_mae": errors.mean().item(),
                "hold_feedback_normalized_mae": persistence.mean().item(),
                "right_grip_mae_native": grp.mean().item(),
                "right_grip_hold_mae_native": (persistence[:,:,-1]*ass[-1]).mean().item(),
                "hold_right_grip_changing_mae_native": (
                    (persistence[moving,:,-1]*ass[-1]).mean().item() if moving.any() else None),
                "per_episode": {i: {"normalized_mae": errors[episode_ids==i].mean().item(),
                                    "hold_mae": persistence[episode_ids==i].mean().item()}
                                for i in ids},
                "right_grip_rising": directional(truth[:,-1]-truth[:,0] > .2*ass[-1]),
                "right_grip_falling": directional(truth[:,0]-truth[:,-1] > .2*ass[-1]),
                "right_grip_changing_windows": int(moving.sum()),
                "right_grip_changing_mae_native": grp[moving].mean().item() if moving.any() else None}
    start = time.monotonic()
    policy.eval()
    groups = (("train", train_ids), ("validation", val_ids))
    before = {key: evaluate(ids) for key, ids in groups}
    reference_eval = None
    if reference:
        ck = torch.load(reference, map_location="cuda")
        assert not set(ck["config"]["train"] + ck["config"]["validation"]).intersection(val_ids)
        assert ck["config"]["manifest"]["source"] == manifest["source"]
        with torch.random.fork_rng(devices=[torch.cuda.current_device()]):
            ref = Policy().cuda().eval()
        ref.load_state_dict(ck["model"])
        reference_eval = evaluate(val_ids, ref, ck)
    matched_steps = None
    losses = []
    policy.train()
    for step in range(steps):
        x, s, a = batch([train[j] for j in rng.integers(len(train), size=B)])
        t = torch.randint(0, 32, (B,), device="cuda")
        eps = torch.randn_like(a)
        pred = policy.unet(sched.add_noise(a, eps, t), t, global_cond=policy.condition(x, s))
        loss = nn.functional.mse_loss(pred, eps)
        if not torch.isfinite(loss):
            raise RuntimeError("nonfinite training loss")
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        losses.append(loss.item())
        if (step+1) % 100 == 0:
            print(step+1, np.mean(losses[-100:]), flush=True)
        if reference and step+1 == ck["config"]["steps"] and step+1 != steps:
            policy.eval()
            matched_steps = {"steps": step+1, "validation": evaluate(val_ids)}
            policy.train()
    policy.eval()
    after = {key: evaluate(ids) for key, ids in groups}
    report = {"train": train_ids, "validation": val_ids, "train_windows": len(train),
              "validation_windows": len(windows(val_ids)), "manifest": manifest, "steps": steps,
              "reference_checkpoint": str(reference) if reference else None,
              "reference_validation": reference_eval, "matched_steps": matched_steps,
              "batch": B, "seed": SEED, "obs_steps": 2, "future_steps": H,
              "loss_first50": np.mean(losses[:50]), "loss_last50": np.mean(losses[-50:]),
              "device": torch.cuda.get_device_name(), "torch": torch.__version__,
              "max_allocated_MiB": torch.cuda.max_memory_allocated()/2**20,
              "max_reserved_MiB": torch.cuda.max_memory_reserved()/2**20,
              "elapsed_train_and_eval_s": time.monotonic()-start,
              "before": before, "after": after}
    torch.save({"model": policy.state_dict(), "obs_center": sc, "obs_scale": ss,
                "action_center": ac, "action_scale": ass, "config": report}, out/"pilot.pt")
    (out/"metrics.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(after), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("data", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--steps", type=int, default=600)
    p.add_argument("--reference", type=Path)
    args = p.parse_args()
    main(args.data, args.out, args.steps, args.reference)
