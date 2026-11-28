"""Pin this cohort's evaluated artifacts and exercise the offline operator entry point."""
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import yaml

from motion_arm import LEFT_J5_PREDICTION_BOUND, accept_target
from cohort_tracking import tracking


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "vr_teleoperation/outputs/dp_20261128_202032"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish():
    data = BASE/"data"
    manifest = json.loads((data/"manifest.json").read_text())
    summary = json.loads((BASE/"train_pair_summary.json").read_text())
    ref = json.loads((BASE/"reference.json").read_text())
    i, t = ref["episode"], ref["frame"]
    if i not in manifest["split"]["train"] or manifest["split"] != summary["split"]:
        raise ValueError("Release reference and evaluation split disagree")
    if summary["steps_each"] != 10_000:
        raise ValueError("Not the requested matched 10k run")
    with np.load(data/f"ep{i}.npz", allow_pickle=False) as z:
        q = z["obs"][t].astype("<f4")
        if hashlib.sha256(q.tobytes()).hexdigest() != ref["pose_sha256"]:
            raise ValueError("Start sample changed")
    src = Path(manifest["source"])
    cfgpath = src.parent/".capture-config"/src.name/(manifest["capture_contract"]["start_profile"]+".yaml")
    cfg = yaml.safe_load(cfgpath.read_text())
    limits = np.vstack([cfg["joint_limits"][side+"_arm"] for side in ("left", "right")])
    rows, failed, guard_rows = [], [], []
    for arch in ("cnn", "transformer"):
        folder = BASE/arch
        stats = json.loads((folder/"metrics.json").read_text())
        if stats["config"]["manifest"] != manifest or not np.isfinite(stats["model"]["normalized_mae"]):
            raise ValueError(f"Invalid {arch} evaluation")
        release = folder/"release.json"
        pins = dict(arch=arch, checkpoint_sha256=sha(folder/"pilot.pt"),
                    manifest_sha256=sha(data/"manifest.json"), episode=i, frame=t, pose_sha256=ref["pose_sha256"])
        with release.open("x") as f:
            json.dump(pins, f, indent=2)
        try:
            with (folder/"offline_release_check.log").open("x") as log, (folder/"offline_release_stderr.log").open("x") as err:
                subprocess.run(["bash", str(ROOT/"run_dp.sh"), "--new-"+arch, "--inspect-only"],
                               cwd=ROOT, check=True, stdout=log, stderr=err, timeout=180)
            smoke = json.loads((folder/"offline_release_check.log").read_text())
            pred = smoke["prediction"]
            if pred["device"] != "cuda" or not pred["cuda_graph"]:
                raise ValueError("Expected the workbench CUDA graph, not CPU fallback")
            if pred["warm_p95_ms"] >= 100:
                raise ValueError("Warmed offline inference did not meet the existing 10 Hz period")
            for side, sl in (("left",slice(0,8)),("right",slice(8,16))):
                accept_target(pred["first_target_rad"][sl], q[sl], limits[sl], 1, 1, side=side)
            with np.load(folder/"eval.npz") as z:
                p, indices = z["prediction"][:,:,0,:], z["ep_t"]
            obs = {}
            for ep in manifest["split"]["validation"]:
                with np.load(data/f"ep{ep}.npz") as z:
                    obs[ep] = z["obs"]
            current = np.stack([obs[ep][j] for ep,j in indices])
            bound = np.tile([.35]*7+[1.05], 2)
            bound[4] = LEFT_J5_PREDICTION_BOUND
            rejected = np.any((np.abs(p-current)>bound) | (p<limits[:,0]) | (p>limits[:,1]), axis=-1)
            guard_rows.append(f"- {arch}：示范起点首预测通过既有左右臂目标门控；"
                              f"留出数据 {len(indices)} 个独立锚点 × {len(p)} 次采样，"
                              f"其中 {rejected.sum()}/{rejected.size} 个首步会被目标门控拒绝。"
                              f"本机预热后推理中位 {pred['warm_median_ms']:.1f} ms、P95 {pred['warm_p95_ms']:.1f} ms。")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            release.rename(folder/"release.failed.json")
            failed.append(f"{arch}: {exc}")
            guard_rows.append(f"- {arch}：未发布，原因：{exc}。")
        m = stats["model"]
        rows.append(f"| {arch} | {m['normalized_mae']:.4f} | {m['arm_mae_native']:.4f} | "
                    f"{m['gripper_mae_native']:.4f} | {'通过' if release.exists() else '未通过'} |")
    hold = summary["hold"]
    rows.append(f"| 保持当前反馈 | {hold['normalized_mae']:.4f} | {hold['arm_mae_native']:.4f} | "
                f"{hold['gripper_mae_native']:.4f} | 对照 |")
    logs = tracking(BASE)
    from eval_ee import compare
    ee = compare(BASE)
    j4 = [logs[s]["j1_to_j7_absolute_error_median_deg"][3] for s in ("left","right")]
    timing = {int(ep):json.loads((BASE/f"audit/ep{ep}.json").read_text())["causal_grid_20hz"]
              for ep in manifest["episodes"]}
    outside = [k for k, v in timing.items() if not v["within_inspection_bounds"]]
    worst = max(max(v["max_age_ms"].values()) for v in manifest["episodes"].values())
    exclusions = {ep: ("原始 episode 目录缺失" if not (src/"episodes"/ep).is_dir() else reason)
                  for ep, reason in manifest["rejected"].items()}
    content = f"""# 新采集批次：视觉 DP 试训

只使用 `{Path(manifest['source']).name}`，不混旧 49 条、其他会话或失败原件。
训练集：{manifest['split']['train']}。留出集：{manifest['split']['validation']}。
排除：{exclusions}。原始 JPEG 哈希、解码和时间检查见 `audit/ep*.json`；
每个已转换示范的四视角抽帧见 `data/preview*.jpg`。记录器 success 不等于人工逐条验收。
按旧训练口径在 10 Hz 网格只取过去样本，纳入轨迹的最大样本年龄为 {worst:.1f} ms（要求 <100 ms）。
独立的 20 Hz 诊断更严（相机年龄/跨视角差 ≤50 ms，关节年龄 ≤20 ms），超出该诊断目标的编号：{outside}；
并非宣称所有反馈达到 20 ms 或已经同步曝光。

两种模型使用同一批四图、关节反馈和 IK 示范目标，从头各训练 10,000 步。
完整留出轨迹评估；三次采样不是三倍独立样本。此为同会话验证，不是新场景实机成功率。

| 路线 | 8步归一化 MAE | 14轴 MAE (rad) | 双夹爪 MAE (rad) | 离线入口 |
| --- | ---: | ---: | ---: | --- |
{chr(10).join(rows)}

{chr(10).join(guard_rows)}

按固定 V2 模型对记录目标和预测首步做正运动学，完整留出集两侧末端控制点平均距离（左/右，cm）：
CNN {ee['cnn'][0]:.2f}/{ee['cnn'][1]:.2f}，Transformer {ee['transformer'][0]:.2f}/{ee['transformer'][1]:.2f}，保持反馈 {ee['hold'][0]:.2f}/{ee['hold'][1]:.2f}。非实际末端、非自由滚动。
![留出轨迹示例](validation_paths.png)

起点取训练示范 {i} 的第 {t} 帧实测反馈，四视角原图与模型输入逐像素核对。
![训练示范起点](reference_views.jpg)

本批纳入的录制段内，按反馈时间向前匹配同侧最近已派发目标（相距小于 100 ms），
左/右 J4 绝对跟踪误差中位数分别为 {j4[0]:.2f}° / {j4[1]:.2f}°。这是示范实录，不是新策略执行效果，也不是零补偿对照。

训练期间模型不学习输出重力：动作仍是 16 维位置目标；普通真机 DP 使用原 pedestal PD、
J1–J7 首条位置指令 100% 模型前馈，夹爪不加。②官方预设位、③所选示范起点、④执行策略仍由操作者分开确认。
设备、推理时限、场景一致性、碰撞和任务成功率均未实机验证。
"""
    with (BASE/"试训结果.md").open("x") as f:
        f.write(content)
    if failed:
        raise RuntimeError("; ".join(failed))


if __name__ == "__main__":
    publish()
