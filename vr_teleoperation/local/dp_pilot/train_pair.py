"""Train and compare fresh CNN and Transformer routes on one offline dataset.

This script only reads ``BASE/data`` and writes research artifacts below
``BASE/{cnn,transformer}``.  It does not import or open any robot interfaces.
Offline validation metrics are not hardware success rates.
"""
import argparse
import fcntl
import json
from pathlib import Path
import time

import numpy as np
import torch
from diffusers.schedulers.scheduling_ddpm import DDPMScheduler

from grasp_routes import Route


H = 8
B = 16
OBS_STEPS = 2
DDPM_STEPS = 32
DEFAULT_STEPS = 10_000
DEFAULT_SEED = 723
EVAL_SEEDS = (724, 725, 726)
JOINT_NAMES = tuple(
    [f"left_J{i}" for i in range(1, 8)]
    + ["left_gripper"]
    + [f"right_J{i}" for i in range(1, 8)]
    + ["right_gripper"]
)
ARM_INDICES = np.array([*range(7), *range(8, 15)])
GRIP_INDICES = np.array([7, 15])


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def append_jsonl(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value, allow_nan=False) + "\n")
        stream.flush()


def load_dataset(base):
    data_dir = base / "data"
    manifest = json.loads((data_dir / "manifest.json").read_text())
    if manifest.get("hz") != 10:
        raise ValueError("expected a 10 Hz prepared dataset")
    expected_cameras = ["global", "chest", "wrist_left", "wrist_right"]
    if list(manifest.get("camera_order", ())) != expected_cameras:
        raise ValueError("unexpected camera order")
    split = manifest.get("split")
    if not split:
        raise ValueError("manifest has no fixed train/validation split")
    train_ids = [int(i) for i in split["train"]]
    validation_ids = [int(i) for i in split["validation"]]
    all_ids = train_ids + validation_ids
    if (
        not train_ids
        or not validation_ids
        or set(train_ids) & set(validation_ids)
        or len(set(all_ids)) != len(all_ids)
    ):
        raise ValueError("invalid train/validation split")
    episodes = {int(i) for i in manifest["episodes"]}
    if set(all_ids) != episodes:
        raise ValueError("split must cover every prepared episode exactly once")

    data = {}
    for episode_id in train_ids + validation_ids:
        path = data_dir / f"ep{episode_id}.npz"
        with np.load(path) as archive:
            required = {"time_ns", "rgb", "obs", "action"}
            if not required <= set(archive.files):
                raise ValueError(f"{path}: missing arrays")
            time_ns = archive["time_ns"]
            rgb = archive["rgb"]
            obs = archive["obs"]
            action = archive["action"]
            if (
                len(time_ns) < H + OBS_STEPS
                or not np.all(np.diff(time_ns) == 100_000_000)
                or rgb.shape != (len(time_ns), 4, 54, 96, 3)
                or obs.shape != (len(time_ns), 16)
                or action.shape != (len(time_ns), 16)
                or rgb.dtype != np.uint8
                or not np.isfinite(obs).all()
                or not np.isfinite(action).all()
            ):
                raise ValueError(f"{path}: incompatible old-prepare arrays")
            data[episode_id] = {
                "rgb": torch.from_numpy(rgb.copy()),
                "obs": torch.from_numpy(obs.astype(np.float32, copy=True)),
                "action": torch.from_numpy(action.astype(np.float32, copy=True)),
            }
    return manifest, train_ids, validation_ids, data


def training_scales(data, train_ids):
    def scale(key):
        low = np.full(16, np.inf, dtype=np.float32)
        high = np.full(16, -np.inf, dtype=np.float32)
        for episode_id in train_ids:
            values = data[episode_id][key].numpy()
            low = np.minimum(low, values.min(axis=0))
            high = np.maximum(high, values.max(axis=0))
        center = (high + low) / 2
        half_range = np.maximum((high - low) / 2, 0.01)
        return torch.from_numpy(center), torch.from_numpy(half_range)

    obs_center, obs_scale = scale("obs")
    action_center, action_scale = scale("action")
    return obs_center, obs_scale, action_center, action_scale


def windows(data, ids):
    return [
        (episode_id, anchor)
        for episode_id in ids
        for anchor in range(1, len(data[episode_id]["obs"]) - H)
    ]


def make_batch(data, pairs, norms, device):
    obs_center, obs_scale, action_center, action_scale = norms
    rgb = torch.stack(
        [data[episode_id]["rgb"][anchor - 1 : anchor + 1] for episode_id, anchor in pairs]
    ).to(device)
    obs = torch.stack(
        [data[episode_id]["obs"][anchor - 1 : anchor + 1] for episode_id, anchor in pairs]
    ).to(device)
    action = torch.stack(
        [
            data[episode_id]["action"][anchor + 1 : anchor + 1 + H]
            for episode_id, anchor in pairs
        ]
    ).to(device)
    return (
        rgb,
        (obs - obs_center) / obs_scale,
        (action - action_center) / action_scale,
    )


def summarize_predictions(prediction, truth, ep_t, action_scale):
    """Return native-unit offline errors; prediction is draws x windows x H x 16."""
    prediction = np.asarray(prediction, dtype=np.float32)
    truth = np.asarray(truth, dtype=np.float32)
    if prediction.ndim == 3:
        prediction = prediction[None]
    if prediction.ndim != 4 or truth.shape != prediction.shape[1:]:
        raise ValueError("prediction/truth shape mismatch")
    error = np.abs(prediction - truth[None])
    per_joint = error.mean(axis=(0, 1, 2))
    truth_step = np.diff(truth, axis=1)
    pred_step = np.diff(prediction, axis=2)
    step_error = np.abs(pred_step - truth_step[None])
    truth_displacement = truth[:, -1] - truth[:, 0]
    pred_displacement = prediction[:, :, -1] - prediction[:, :, 0]
    moving = np.max(np.abs(truth - truth[:, :1]), axis=(1, 2)) > 0.035
    directional = np.abs(truth_displacement) > 0.035
    direction_accuracy = (
        float(
            (
                np.sign(pred_displacement)
                == np.sign(truth_displacement)[None]
            )[:, directional].mean()
        )
        if directional.any()
        else None
    )

    def subset(mask):
        if not np.any(mask):
            return {"windows": 0, "mae_native": None}
        return {
            "windows": int(mask.sum()),
            "mae_native": float(error[:, mask].mean()),
            "arm_mae_native": float(error[:, mask][:, :, :, ARM_INDICES].mean()),
            "gripper_mae_native": float(error[:, mask][:, :, :, GRIP_INDICES].mean()),
        }

    episode_ids = np.asarray(ep_t)[:, 0]
    return {
        "draws": int(len(prediction)),
        "windows": int(len(truth)),
        "mae_native": float(error.mean()),
        "normalized_mae": float((error / action_scale[None, None, None]).mean()),
        "arm_mae_native": float(error[:, :, :, ARM_INDICES].mean()),
        "gripper_mae_native": float(error[:, :, :, GRIP_INDICES].mean()),
        "endpoint_mae_native": float(error[:, :, -1].mean()),
        "motion_step_mae_native": float(step_error.mean()),
        "motion_displacement_mae_native": float(
            np.abs(pred_displacement - truth_displacement[None]).mean()
        ),
        "motion_threshold_native": 0.035,
        "motion_direction_accuracy": direction_accuracy,
        "moving": subset(moving),
        "stationary": subset(~moving),
        "per_joint_mae_native": {
            name: float(value) for name, value in zip(JOINT_NAMES, per_joint)
        },
        "per_draw_mae_native": [float(error[i].mean()) for i in range(len(error))],
        "per_episode": {
            str(episode_id): subset(episode_ids == episode_id)
            for episode_id in sorted(set(episode_ids.tolist()))
        },
    }


def plot_training(path, losses, arch):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    values = np.asarray(losses)
    width = min(100, len(values))
    rolling = np.convolve(values, np.ones(width) / width, mode="valid")
    fig, axis = plt.subplots(figsize=(9.2, 4.3), constrained_layout=True)
    axis.plot(np.arange(1, len(values) + 1), values, color="#9db7b4", alpha=0.28, lw=0.65)
    axis.plot(
        np.arange(width, len(values) + 1),
        rolling,
        color="#006f68",
        lw=1.8,
        label=f"{width}-step mean",
    )
    axis.set(title=f"{arch.upper()} training loss", xlabel="optimizer step", ylabel="noise MSE")
    axis.grid(alpha=0.2)
    axis.legend()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_prediction(path, prediction, truth, hold, ep_t, arch):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    movement = np.max(np.abs(truth - truth[:, :1]), axis=(1, 2))
    row = int(np.argmax(movement))
    dims = (3, 7, 11, 15)
    steps = np.arange(1, H + 1)
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.2), constrained_layout=True)
    for axis, dim in zip(axes.flat, dims):
        axis.plot(steps, truth[row, :, dim], "o-", color="black", lw=2, label="recorded target")
        axis.plot(steps, hold[row, :, dim], "--", color="#777777", lw=1.5, label="hold")
        for draw_index, values in enumerate(prediction[:, row, :, dim]):
            axis.plot(
                steps,
                values,
                color="#007d83",
                alpha=0.38,
                lw=1.3,
                label="DDPM draws" if draw_index == 0 else None,
            )
        axis.set(title=JOINT_NAMES[dim], xlabel="future step", ylabel="native value")
        axis.grid(alpha=0.2)
    axes.flat[0].legend(fontsize=8)
    episode_id, anchor = ep_t[row]
    fig.suptitle(
        f"{arch.upper()} offline validation example: episode {episode_id}, anchor {anchor}"
    )
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_comparison(path, summaries):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ("all action", "arm joints", "grippers", "motion step")
    fields = ("mae_native", "arm_mae_native", "gripper_mae_native", "motion_step_mae_native")
    x = np.arange(len(labels))
    width = 0.24
    fig, axis = plt.subplots(figsize=(9.4, 4.8), constrained_layout=True)
    for offset, (name, color) in enumerate(
        (("cnn", "#007d83"), ("transformer", "#d97725"), ("hold", "#777777"))
    ):
        source = summaries[name]
        axis.bar(
            x + (offset - 1) * width,
            [source[field] for field in fields],
            width,
            label=name,
            color=color,
        )
    axis.set(
        title="Offline full-validation comparison (not hardware success)",
        ylabel="native-unit MAE",
        xticks=x,
        xticklabels=labels,
    )
    axis.grid(axis="y", alpha=0.2)
    axis.legend()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def train_and_evaluate(
    arch,
    out,
    steps,
    seed,
    manifest,
    train_ids,
    validation_ids,
    data,
    norms_cpu,
):
    device = torch.device("cuda")
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    route = Route(arch == "transformer").to(device)
    optimizer = torch.optim.AdamW(route.parameters(), lr=0.0005)
    scheduler = DDPMScheduler(
        num_train_timesteps=DDPM_STEPS,
        beta_schedule="squaredcos_cap_v2",
        prediction_type="epsilon",
        clip_sample=True,
    )
    norms = tuple(value.to(device) for value in norms_cpu)
    train_windows = windows(data, train_ids)
    validation_windows = windows(data, validation_ids)
    np_rng = np.random.default_rng(seed)
    noise_rng = torch.Generator(device=device).manual_seed(seed + 10_000)
    loss_tensors = []
    progress = out / "progress.jsonl"
    started = time.monotonic()
    append_jsonl(
        progress,
        {
            "status": "training",
            "arch": arch,
            "step": 0,
            "steps": steps,
            "train_windows": len(train_windows),
            "time": time.time(),
        },
    )
    route.train()
    for step in range(steps):
        indices = np_rng.integers(len(train_windows), size=B)
        pairs = [train_windows[index] for index in indices]
        rgb, obs, action = make_batch(data, pairs, norms, device)
        timestep = torch.randint(
            0, DDPM_STEPS, (B,), device=device, generator=noise_rng
        )
        epsilon = torch.randn(
            action.shape, device=device, dtype=action.dtype, generator=noise_rng
        )
        noisy = scheduler.add_noise(action, epsilon, timestep)
        estimate = route.noise(noisy, timestep, route.condition(rgb, obs))
        loss = torch.nn.functional.mse_loss(estimate, epsilon)
        if not torch.isfinite(loss):
            raise RuntimeError(f"{arch}: nonfinite training loss at step {step + 1}")
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        loss_tensors.append(loss.detach())
        if (step + 1) % 100 == 0 or step + 1 == steps:
            loss_100 = float(torch.stack(loss_tensors[-100:]).mean().cpu())
            row = {
                "status": "training",
                "arch": arch,
                "step": step + 1,
                "steps": steps,
                "loss_100": loss_100,
                "elapsed_s": time.monotonic() - started,
                "time": time.time(),
            }
            append_jsonl(progress, row)
            print(
                arch,
                f"{step + 1}/{steps}",
                f"loss={row['loss_100']:.5f}",
                flush=True,
            )

    losses = torch.stack(loss_tensors).cpu().numpy()
    train_elapsed = time.monotonic() - started
    route.eval()
    scheduler.set_timesteps(DDPM_STEPS)
    predictions = []
    truth = np.stack(
        [
            data[episode_id]["action"][anchor + 1 : anchor + 1 + H].numpy()
            for episode_id, anchor in validation_windows
        ]
    )
    hold = np.stack(
        [
            np.repeat(
                data[episode_id]["obs"][anchor].numpy()[None],
                H,
                axis=0,
            )
            for episode_id, anchor in validation_windows
        ]
    )
    append_jsonl(
        progress,
        {
            "status": "evaluating",
            "arch": arch,
            "draws": list(EVAL_SEEDS),
            "validation_windows": len(validation_windows),
            "time": time.time(),
        },
    )
    with torch.inference_mode():
        for draw_index, draw_seed in enumerate(EVAL_SEEDS):
            generator = torch.Generator(device=device).manual_seed(draw_seed)
            rows = []
            for offset in range(0, len(validation_windows), B):
                pairs = validation_windows[offset : offset + B]
                rgb, obs, action = make_batch(data, pairs, norms, device)
                condition = route.condition(rgb, obs)
                sample = torch.randn(
                    action.shape,
                    device=device,
                    dtype=action.dtype,
                    generator=generator,
                )
                for timestep in scheduler.timesteps:
                    estimate = route.noise(sample, timestep.to(device), condition)
                    sample = scheduler.step(
                        estimate,
                        int(timestep),
                        sample,
                        generator=generator,
                    ).prev_sample
                rows.append(
                    (
                        sample * norms[3] + norms[2]
                    ).cpu().numpy()
                )
            predictions.append(np.concatenate(rows))
            append_jsonl(
                progress,
                {
                    "status": "evaluating",
                    "arch": arch,
                    "draw": draw_index + 1,
                    "draw_seed": draw_seed,
                    "time": time.time(),
                },
            )
            print(arch, f"evaluation draw {draw_index + 1}/{len(EVAL_SEEDS)}", flush=True)
    prediction = np.stack(predictions)
    if not np.isfinite(prediction).all():
        raise RuntimeError(f"{arch}: nonfinite validation prediction")
    ep_t = np.asarray(validation_windows, dtype=np.int64)
    action_scale = norms_cpu[3].numpy()
    model_metrics = summarize_predictions(prediction, truth, ep_t, action_scale)
    hold_metrics = summarize_predictions(hold, truth, ep_t, action_scale)
    elapsed = time.monotonic() - started
    config = {
        "scope": "offline full validation only; no rollout or hardware success claim",
        "arch": arch,
        "steps": steps,
        "seed": seed,
        "batch": B,
        "obs_steps": OBS_STEPS,
        "future_steps": H,
        "ddpm_steps": DDPM_STEPS,
        "evaluation_seeds": list(EVAL_SEEDS),
        "train": train_ids,
        "validation": validation_ids,
        "train_windows": len(train_windows),
        "validation_windows": len(validation_windows),
        "params": sum(parameter.numel() for parameter in route.parameters()),
        "loss_first100": float(np.mean(losses[:100])),
        "loss_last100": float(np.mean(losses[-100:])),
        "train_elapsed_s": train_elapsed,
        "total_elapsed_s": elapsed,
        "device": torch.cuda.get_device_name(device),
        "torch": torch.__version__,
        "manifest": manifest,
    }
    checkpoint = {
        "model": {key: value.detach().cpu() for key, value in route.state_dict().items()},
        "obs_center": norms_cpu[0],
        "obs_scale": norms_cpu[1],
        "action_center": norms_cpu[2],
        "action_scale": norms_cpu[3],
        "config": config,
    }
    torch.save(checkpoint, out / "pilot.pt")
    np.savez_compressed(
        out / "eval.npz",
        prediction=prediction,
        truth=truth,
        hold=hold,
        ep_t=ep_t,
        draws=np.asarray(EVAL_SEEDS),
    )
    metrics = {"config": config, "model": model_metrics, "hold": hold_metrics}
    write_json(out / "metrics.json", metrics)
    plot_training(out / "training_curve.png", losses, arch)
    plot_prediction(
        out / "validation_prediction.png",
        prediction,
        truth,
        hold,
        ep_t,
        arch,
    )
    append_jsonl(
        progress,
        {
            "status": "complete",
            "arch": arch,
            "elapsed_s": elapsed,
            "mae_native": model_metrics["mae_native"],
            "time": time.time(),
        },
    )
    return metrics


def run(base, steps, seed):
    if steps < 1:
        raise ValueError("--steps must be positive")
    base = base.resolve()
    targets = [
        base / "cnn",
        base / "transformer",
        base / "train_pair_summary.json",
        base / "train_pair_comparison.png",
    ]
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError("refuse to overwrite: " + ", ".join(existing))
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for training; no training was started")

    manifest, train_ids, validation_ids, data = load_dataset(base)
    norms_cpu = training_scales(data, train_ids)
    for directory in targets[:2]:
        directory.mkdir(parents=False, exist_ok=False)
    for directory in targets[:2]:
        write_json(directory / "input_manifest.json", manifest)

    torch.set_num_threads(2)
    results = {}
    for arch in ("cnn", "transformer"):
        try:
            results[arch] = train_and_evaluate(
                arch,
                base / arch,
                steps,
                seed,
                manifest,
                train_ids,
                validation_ids,
                data,
                norms_cpu,
            )
        except Exception as exc:
            append_jsonl(
                base / arch / "progress.jsonl",
                {
                    "status": "failed",
                    "arch": arch,
                    "error": repr(exc),
                    "time": time.time(),
                },
            )
            raise
        torch.cuda.empty_cache()
    hold = results["cnn"]["hold"]
    if hold != results["transformer"]["hold"]:
        raise RuntimeError("hold comparator changed between routes")
    summary = {
        "scope": "offline full validation only; no rollout or hardware success claim",
        "steps_each": steps,
        "seed": seed,
        "evaluation_seeds": list(EVAL_SEEDS),
        "split": {"train": train_ids, "validation": validation_ids},
        "cnn": results["cnn"]["model"],
        "transformer": results["transformer"]["model"],
        "hold": hold,
    }
    write_json(base / "train_pair_summary.json", summary)
    plot_comparison(
        base / "train_pair_comparison.png",
        {"cnn": summary["cnn"], "transformer": summary["transformer"], "hold": hold},
    )
    print(json.dumps(summary, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path, help="dataset/output root containing data/manifest.json")
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    lock_path = args.base.resolve() / ".train_pair.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.base, args.steps, args.seed)


if __name__ == "__main__":
    main()
