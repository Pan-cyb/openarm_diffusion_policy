# DP Week1 交付报告

日期：2026-10-24

结论：**本机 DP W1 运行验收完成；论文性能与六人跨机器验收未完成**

## 验证入口

```bash
bash scripts/install_dp.sh --clean-local-uv-cache
bash scripts/verify_week1.sh
```

最终复验退出码为 0；pytest 80/80、环境检查 11/11，环境报告记录 105 个 distributions。

## OpenArm

- 目录：`/media/pan/Expansion/dp_week1/outputs/openarm-week1-20261024-005817-+0800/`
- 30.000 秒、600 帧、20 fps、640×480。
- 14/14 gates 为 true，0 MuJoCo warning。
- 双臂 joint3 excursion 约 `0.19999 rad`；双夹爪约 `0.023999 m`。
- 视频通过解码、独立 ffprobe 和人工目视检查。
- 自动选择前，运行时将官方 front/side 两个候选相机的 FOV 从 90°改为 35°，最终选择 side camera；相机位姿、dynamics、source 与 trace 不变。
- 这是基础诊断，不是 learned OpenArm policy。

## Diffusion Policy

- 目录：`/media/pan/Expansion/dp_week1/outputs/dp-official-20261024-005826-+0800/`
- checkpoint：`4293295813 B`
- SHA-256：`bac7221f7e34cd51162dc1972e1a39ffcddc87de1dc1780c44ffa61b88c4ff76`
- SHA-256 为本地计算值，不是发布者签名。
- 原生 `[512,1024,2048]` 架构、100 DDPM 步、EMA-only 资源适配加载、原始 `PushTImageRunner`、单环境。

| Seed | Native score | 视频验证 |
|---|---:|---|
| `10000` | `0` | 300 帧、30 秒、10 fps |
| `10001` | `0.9520230291535516` | 300 帧、30 秒、10 fps |
| `10002` | `0.696411764381858` | 300 帧、30 秒、10 fps |
| Mean | `0.5494782645118033` | 不是正式成功率 |

三个回合均未达到原始 PushT completion threshold，必须保留为失败案例。结果只证明预训练推理链路可运行，不证明策略性能成功或论文基准复现。

## 额外固定诊断

- 输出：`/media/pan/Expansion/dp_week1/outputs/dp-official-extra-20261024-011500/`
- 运行退出码：0
- 约束：相同 checkpoint、相同参数、无训练、无调参、无种子筛选。

| Seed | Native score | 原始完成条件 | 视频 |
|---|---:|---|---|
| `10003` | `0.7105543278081632` | 未达到 | `scionrs8.mp4`，300 帧、30 秒 |
| `10004` | `1.0` | 已达到 | `9du6rtbj.mp4`，199 帧、19.9 秒 |
| `10005` | `1.0` | 已达到 | `aneglm8f.mp4`，235 帧、23.5 秒 |

额外批次均值为 `0.9035181092693877`。种子 `10004`、`10005` 达到原始 PushT 95% coverage 条件并在 300 步上限前终止。

合并两个批次后，共 6 个开发场景：2 条完成、4 条未完成。
第二批是在首批结束后追加的功能诊断，每批种子在执行前固定。
该计数不是正式成功率，不等同于论文原始评估协议或 PDF 要求的至少 30 次正式测试；
初始三条失败保持不变。

## 资源

- DP wall time：`441.09 s`
- CUDA peak allocated：约 `1123 MiB`
- DP cgroup RAM：约 `5.295 GiB`
- 额外批次 wall time：`407.69 s`
- 额外批次 CUDA peak allocated：约 `1123 MiB`
- 额外批次 cgroup RAM：约 `5.27 GiB`
- OpenArm cgroup RAM：约 `1.902 GiB`
- 环境检查最高 cgroup RAM：约 `6.411 GiB`
- 根分区可用：约 `33.26 GiB`
- 项目目录：约 `4.6 GiB`
- 外盘 `dp_week1`：约 `4.2 GiB`

所有运行均低于 8 GiB memory guard。当前无剩余 guard 或模型任务。

## 范围声明

- 没有从头训练、训练数据、π0/OpenPI、handover、W2/W3 或硬件工作。
- 没有完成论文性能复现或跨机器验收。
- 只验证当前这一台笔记本。
- W1 已关闭；任何下一阶段必须由用户明确批准。
