"""Offline-only deployment rehearsal. No robot transport or command publisher."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler

from train import Policy
from shadow_inputs import PERIOD_NS, VIEWS


WEIGHT_SHA = "3b2f300753ef66d3d80ac1794f87c2c7dee031721b80de0e7721f66188b6add6"


class ShadowPolicy:
    def __init__(self, checkpoint, device, use_graph=False, release=None):
        self.device = torch.device(device)
        with checkpoint.open("rb") as f:
            expected = WEIGHT_SHA if release is None else release.checkpoint_sha256
            if hashlib.sha256(f.read()).hexdigest() != expected:
                raise ValueError("checkpoint does not match the selected deployment release")
            f.seek(0)
            ck = torch.load(f, map_location=self.device)
        self.cfg = ck["config"]
        self.release = release
        if release is not None and not release.legacy:
            from grasp_routes import Route
            if checkpoint.resolve() != release.checkpoint.resolve():
                raise ValueError("checkpoint path is outside the selected deployment release")
            if self.cfg.get("manifest") != release.manifest:
                raise ValueError("checkpoint config.manifest does not match selected release")
            self.model = Route(release.arch == "transformer").to(self.device).eval()
            self.transformer = release.arch == "transformer"
        else:
            self.model = Policy().to(self.device).eval()
            self.transformer = False
        self.model.load_state_dict(ck["model"], strict=True)
        self.sc, self.ss, self.ac, self.ascale = (
            ck[k] for k in ("obs_center", "obs_scale", "action_center", "action_scale"))
        self.sc, self.ss, self.ac, self.ascale = (
            torch.as_tensor(value, device=self.device)
            for value in (self.sc, self.ss, self.ac, self.ascale))
        if any(value.shape != (16,) or not torch.isfinite(value).all()
               for value in (self.sc, self.ss, self.ac, self.ascale)):
            raise ValueError("checkpoint normalization fields are invalid")
        if not torch.all(self.ss > 0) or not torch.all(self.ascale > 0):
            raise ValueError("checkpoint normalization scales must be positive")
        self.scheduler = DDPMScheduler(num_train_timesteps=32, beta_schedule="squaredcos_cap_v2",
                                       prediction_type="epsilon", clip_sample=True)
        self.scheduler.set_timesteps(32)
        self.graph = None
        if use_graph:
            if self.device.type != "cuda":
                raise ValueError("CUDA graph requires a CUDA device")
            self.capture_unet()
            if self.transformer:
                # Transformer scheduler kernels have a large one-time CUDA setup cost.
                # Pay it before the workbench can open cameras or arm workers.
                generator = torch.Generator(device=self.device).manual_seed(0)
                self.predict(
                    np.array([0, PERIOD_NS], dtype=np.int64),
                    np.zeros((2, 4, 54, 96, 3), dtype=np.uint8),
                    np.zeros((2, 16), dtype=np.float32),
                    generator,
                )
                torch.cuda.synchronize()

    @torch.inference_mode()
    def capture_unet(self):
        # Only capture the deterministic network; the scheduler and noise stay unchanged.
        self.sample = torch.zeros((1, 8, 16), device=self.device)
        self.cond = torch.zeros((1, 2, 112) if self.transformer else (1, 224),
                                device=self.device)
        self.step = torch.zeros((), dtype=torch.int64, device=self.device)
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                self.noise(self.sample, self.step, self.cond)
        torch.cuda.current_stream().wait_stream(stream)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph):
            self.eps = self.noise(self.sample, self.step, self.cond)
        self.t_device = self.scheduler.timesteps.to(self.device)

    def noise(self, value, step, condition):
        if self.release is not None and not self.release.legacy:
            return self.model.noise(value, step, condition)
        return self.model.unet(value, step, global_cond=condition)

    @torch.inference_mode()
    def predict(self, stamps, rgb, qpos, generator):
        if stamps.shape != (2,) or stamps.dtype != np.int64 or int(stamps[1])-int(stamps[0]) != PERIOD_NS:
            raise ValueError("need two consecutive 10 Hz grid anchors")
        if rgb.dtype != np.uint8 or rgb.shape != (2, 4, 54, 96, 3):
            raise ValueError("invalid RGB history")
        if qpos.dtype != np.float32 or qpos.shape != (2, 16) or not np.isfinite(qpos).all():
            raise ValueError("invalid feedback history")
        to_batch = lambda x: torch.from_numpy(x).to(self.device).unsqueeze(0)
        cond = self.model.condition(to_batch(rgb), (to_batch(qpos)-self.sc)/self.ss)
        v = torch.randn((1, 8, 16), device=self.device, generator=generator)
        if self.graph is not None:
            self.cond.copy_(cond)
        for j, t in enumerate(self.scheduler.timesteps):
            if self.graph is None:
                eps = self.noise(v, t.to(self.device), cond)
            else:
                self.sample.copy_(v)
                self.step.copy_(self.t_device[j])
                self.graph.replay()
                eps = self.eps
            v = self.scheduler.step(eps, int(t), v, generator=generator).prev_sample
        result = (v*self.ascale+self.ac)[0].cpu().numpy()
        if result.shape != (8, 16) or not np.isfinite(result).all():
            raise ValueError("invalid prediction")
        return result


def replay(root, out, device, use_graph=False):
    init = time.perf_counter()
    policy = ShadowPolicy(root/"run/pilot.pt", device, use_graph)
    init_ms = (time.perf_counter()-init)*1000
    manifest = json.loads((root/"data/manifest.json").read_text())
    if manifest != policy.cfg["manifest"] or manifest["camera_order"] != list(VIEWS):
        raise ValueError("training/replay manifest mismatch")
    out.mkdir(parents=True, exist_ok=False)
    rows, timings, worst = {}, [], None
    arm_joints = np.array([*range(7), *range(8, 15)])
    for eid in policy.cfg["validation"]:
        with np.load(root/f"data/ep{eid}.npz", allow_pickle=False) as ep:
            ts, rgb, pos, target = (ep[k] for k in ("time_ns", "rgb", "obs", "action"))
        generator = torch.Generator(device=device).manual_seed(724+eid)
        preds = []
        for i in range(1, len(ts)-8):
            tic = time.perf_counter()
            hist = slice(i-1, i+1)
            result = policy.predict(ts[hist], rgb[hist], pos[hist], generator)
            timings.append((time.perf_counter()-tic)*1000)
            preds.append(result)
            diff = np.abs(result[0, arm_joints]-pos[i, arm_joints])
            j = arm_joints[diff.argmax()]
            if worst is None or diff.max() > worst["absolute_difference"]:
                worst = {"episode": eid, "anchor": i, "channel_zero_based": int(j),
                         "absolute_difference": float(diff.max()), "feedback": float(pos[i,j]),
                         "first_predicted_target": float(result[0,j]),
                         "recorded_next_target": float(target[i+1,j])}
        rows[eid] = len(preds)
        np.savez_compressed(out/f"shadow_ep{eid}.npz", anchor_ns=ts[1:-8],
                            predicted_targets=np.stack(preds))
        print(f"episode {eid}: {len(preds)} offline predictions", flush=True)
    report = {"mode": "recorded_observations_only", "hardware_commands": 0,
              "checkpoint_sha256": WEIGHT_SHA, "device": device, "predictions_by_episode": rows,
              "cuda_graph": use_graph, "initialization_ms": init_ms,
              "compute_ms": {"first": timings[0], "median": float(np.median(timings)),
                             "p95": float(np.percentile(timings, 95)), "max": max(timings),
                             "at_least_100ms": sum(t >= 100 for t in timings)},
              "largest_first_target_minus_feedback_excluding_grippers": worst}
    (out/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    p.add_argument("--cuda-graph", action="store_true")
    args = p.parse_args()
    torch.set_num_threads(2)
    replay(args.baseline, args.output, args.device, args.cuda_graph)
