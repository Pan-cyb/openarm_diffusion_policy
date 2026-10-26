# OpenArm Diffusion Policy Week1

> [!NOTE]
> **2026-10-26 归档检查：81/81 测试通过，上游固定版本核验通过。** 本次没有重新运行模型；下文模型指标来自 2026-10-24 的历史 W1 验收。
>
> 本机 DP 数据已恢复至外盘并校验；本地重复数据随后按用户要求删除，源码和运行环境保留。当前磁盘配置仅适用于本机，移植前必须按实际设备适配，不能绕过守卫。

状态：**历史本机 DP W1 验收完成，当前 DP 存储已恢复；论文性能与六人跨机器验收未完成**。

本独立项目仅验证官方 Diffusion Policy 图像 PushT 预训练推理和 OpenArm MuJoCo v1 基础仿真。不包含 π0/OpenPI、handover、W2/W3、从头训练、真机或 ROS。

## 阅读入口

- [W1 周记与任务书验收对照](docs/weekly/WEEK1_2026-10-20_2026-10-26.md)
- [项目交接手册](docs/HANDOFF.md)
- [技术文档与 CLI](docs/TECHNICAL_DOCUMENTATION.md)
- [当前状态与存储记录](docs/PROJECT_STATUS.md)
- [原始四周任务书](docs/reference/OpenArm_六名学生_四周任务书.pdf)
- [精选证据说明与校验清单](docs/evidence/README.md)

仓库包含必要的小型报告、预览和验收录像；约 4 GB 的 checkpoint、venv、
上游 checkout 和原始输出目录不入库。初次克隆须按技术文档准备依赖和实际存储配置，
不是下载仓库后即可在任意机器直接运行的完整环境镜像。

## 复现命令

以下命令用于后续手动复查或重建；本次文件恢复没有重新运行整套流程。大文件已有校验通过的副本，无需重新下载。

已有环境与 checkpoint 的复查：

```bash
cd /home/pan/openarm_diffusion_policy
bash scripts/verify_week1.sh
```

从零重建：

```bash
cd /home/pan/openarm_diffusion_policy
bash scripts/install_dp.sh --clean-local-uv-cache
bash scripts/run-safe.sh --budget-gib 0.1 --external-budget-gib 0.25 --timeout 300 -- \
  python3 scripts/fetch_dp_checkpoint.py config
bash scripts/run-safe.sh --budget-gib 0.1 --external-budget-gib 5 --timeout 7200 -- \
  python3 scripts/fetch_dp_checkpoint.py checkpoint --connections 4
bash scripts/verify_week1.sh
```

执行前，`config/storage.json` 中的 UUID、父盘序列号、ext4 和挂载点必须与已挂载外盘一致。该流程不格式化或重新分区。当前 `/media/pan/Expansion` 是未挂载的遗留空目录，不能代替 `/media/pan/Expansion1` 使用。

此前 W1 的 `verify_week1.sh` 退出码为 0；存储恢复后尚未重跑该完整入口。

基础复查默认运行 `10000..10002`。复现补充功能演示的完整固定批次：

```bash
bash scripts/run-safe.sh --budget-gib 0.1 --external-budget-gib 0.1 --timeout 1800 -- \
  .venv-dp/bin/python scripts/eval_dp_official.py \
  --test-start-seed 10003 --n-test 3 --n-test-vis 3 \
  --output-dir "/media/pan/Expansion1/dp_week1/outputs/dp-extra-$(date +%Y%m%d-%H%M%S)"
```

## 直接证据

- [2026-10-26 发布测试](docs/evidence/publication/publication-tests-20261026.xml)：81/81；不是新的策略运行。
- [DP 文件恢复校验](docs/evidence/storage/storage-restore-20261024.json)：41/41 文件一致，读写和符号链接通过。
- [后续本地副本清理](docs/evidence/storage/local-dp-backup-cleanup-20261024.json)：两份哈希一致后删除本地 DP 数据副本。
- [存储守卫测试](docs/evidence/storage/storage-guard-tests-20261024.xml)：12/12；下列 W1 结果为历史证据。
- [环境报告](docs/evidence/week1/environment.json)：11/11 检查通过。
- [历史 JUnit](docs/evidence/week1/week1-tests.xml)：80/80 测试通过。
- [OpenArm 报告](docs/evidence/week1/openarm/report.json)
- [OpenArm 30 秒视频](docs/evidence/week1/openarm/openarm-week1.mp4)
- [DP 初始批报告](docs/evidence/week1/dp-initial/report.json)
- [DP 初始批原始指标](docs/evidence/week1/dp-initial/eval_log.json)
- [初始三种子预览](docs/evidence/week1/dp-initial/all-seeds-preview.png)
- [W1 交付报告](docs/WEEK1_REPORT.md)

## 最终结果

### OpenArm

- 14/14 gates 通过，0 MuJoCo warning。
- 30.000 秒、600 帧、20 fps、640×480；视频已解码、ffprobe 和人工检查。
- 双臂 joint3 excursion 约 `0.19999 rad`，双夹爪约 `0.023999 m`。
- 自动选择前，运行时将两个官方候选相机的 FOV 从 90°改为 35°；最终选择 side camera。相机位姿、dynamics、source 和轨迹不变。
- 这是基础诊断，不是 learned OpenArm policy。

### Diffusion Policy

- 官方 checkpoint：`4293295813 B`
- SHA-256：`bac7221f7e34cd51162dc1972e1a39ffcddc87de1dc1780c44ffa61b88c4ff76`
- 哈希为本地计算值，不是发布者签名。
- 保持原生 `[512,1024,2048]` 架构、100 DDPM 步、原始 `PushTImageRunner`；单环境 CUDA 预训练评估。

| Seed | Native score | 原始完成条件 | 视频 |
|---|---:|---|---|
| `10000` | `0` | 未达到 | [300 帧，30 秒](docs/evidence/week1/dp-initial/media/e9ovgu7s.mp4) |
| `10001` | `0.9520230291535516` | 未达到 | [300 帧，30 秒](docs/evidence/week1/dp-initial/media/8b3fw026.mp4) |
| `10002` | `0.696411764381858` | 未达到 | [300 帧，30 秒](docs/evidence/week1/dp-initial/media/m4np8k6l.mp4) |
| `10003` | `0.7105543278081632` | 未达到 | [300 帧，30 秒](docs/evidence/week1/dp-extra/media/scionrs8.mp4) |
| `10004` | `1.0` | 已达到 | [199 帧，19.9 秒](docs/evidence/week1/dp-extra/media/9du6rtbj.mp4) |
| `10005` | `1.0` | 已达到 | [235 帧，23.5 秒](docs/evidence/week1/dp-extra/media/aneglm8f.mp4) |

初始批次均值为 `0.5494782645118033`，额外固定批次均值为 `0.9035181092693877`。种子 `10004`、`10005` 达到原始 PushT 95% coverage 完成条件并提前终止。
原始代码的 reward 为 `clip(coverage / 0.95, 0, 1)`，done 为 `coverage > 0.95`；
因此 native score `0.952` 不是 95.2% coverage，也不表示该回合已经完成。

每个批次的种子都在该批运行前固定；第二批是在首批完成后追加的功能诊断。
两个批次使用相同 checkpoint、权重和参数，无训练、调参或种子筛选。
六场景中 2 条完成、4 条未完成；这不是正式成功率，
也不等同于论文原始评估协议或 PDF 要求的至少 30 次正式测试。

## 资源（历史 W1）

- DP wall time：`441.09 s`
- CUDA peak allocated：约 `1123 MiB`
- DP cgroup RAM：约 `5.295 GiB`
- 额外批次 wall time：`407.69 s`
- 额外批次 CUDA peak allocated：约 `1123 MiB`
- 额外批次 cgroup RAM：约 `5.27 GiB`
- 环境检查最高 cgroup RAM：约 `6.411 GiB`，低于 8 GiB 限制
- 根分区可用：约 `33.26 GiB`
- 项目目录：约 `4.6 GiB`
- 外盘 `dp_week1`：约 `4.2 GiB`

## 历史问题

- uv 在 exFAT 上创建 virtualenv symlink 失败：改用本机 ext4 cache，无全局系统修改。
- 检查点下载曾慢速、中断和 range underflow：保留连续 partial 与失败日志，最终由有界重试恢复完成。
- OpenArm CLI duration bug 已修复并加入回归测试。
- 早期 90° FOV 视频保留；最终视频使用仅影响视图的运行时 FOV 35。

W1 已形成可运行闭环和 2 条功能成功示例，但科学性能仍未认证。当前 DP 文件和存储守卫已恢复，本次没有重跑策略。任何新阶段必须由用户明确批准。
