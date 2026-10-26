# 项目状态

更新日期：2026-10-26

阶段：Week1 收口、文档交接与 Git 归档

状态：**历史本机 DP W1 验收完成，当前 DP 文件与存储守卫已恢复；论文性能与六人跨机器验收未完成**

## 2026-10-26 归档检查

- 已建立本地 `main` 仓库和私有 GitHub 仓库 `Pan-cyb/openarm_diffusion_policy`；实际提交与推送状态以 `git log -1 --oneline` 和 `git ls-remote origin refs/heads/main` 为准。
- 原始[任务书 PDF](reference/OpenArm_六名学生_四周任务书.pdf)已读取并视觉核对，W1 周期为 10 月 20 日至 26 日。只完成当前笔记本的 DP/W1 部分，不替代六人运行验收。
- 当前完整 pytest 在资源守卫内通过 81/81；[发布 JUnit](evidence/publication/publication-tests-20261026.xml)与[资源记录](evidence/publication/resource-guard-tests-20261026.json)保留。本次未重跑 OpenArm、PushT 或完整 `verify_week1.sh`。
- 上游版本已重新核验：Diffusion Policy 与 OpenArm clean，Gym 仅精确的 metadata patch。没有改算法或升级依赖。
- [项目交接](HANDOFF.md)、[技术手册](TECHNICAL_DOCUMENTATION.md)、[W1 周记](weekly/WEEK1_2026-10-20_2026-10-26.md)及[精选证据](evidence/README.md)用于后续接手。
- 归档只包含代码、文档、任务书和小型证据，不包含 venv、上游 checkout、权重、凭据或原始输出目录。未进行磁盘操作，也未启动 W2。

## 2026-10-24 存储恢复

- 用户已手动完成 1 GiB FAT32、256 GiB ext4、2 TiB ext4 和剩余 exFAT；本轮没有格式化、分区或重挂载。
- DP 工作区：`/media/pan/Expansion1/dp_week1`，ext4 UUID `b8104161-e073-d644-a4b9-f4efbcfc1463`，父盘序列号 `00000000NT1FVEEZ`。配置与 AGENTS 已更新，守卫限制未降低。
- `/media/pan/Expansion` 是遗留空目录而非挂载点，不能作为数据目标。本轮不清理或更改挂载名称；以后路径变化必须重新核验配置。
- 已复制 41 个 DP 文件、18 个目录、4,295,070,229 bytes。全部路径、大小和 SHA-256 与原清单一致；用户读写和符号链接测试通过。
- 校验报告：[storage-restore-20261024.json](evidence/storage/storage-restore-20261024.json)；当时 12 项守卫测试：[JUnit](evidence/storage/storage-guard-tests-20261024.xml)。
- 资源日志：复制 `outputs/resources/run-20261024T201020-8769.json`，校验 `outputs/resources/run-20261024T201236-9241.json`，测试 `outputs/resources/run-20261024T201508-9693.json`，均 exit 0。复制约 93 秒，校验约 141 秒。
- 恢复完成时曾保留全部 89 个原文件的内置盘备份；之后按用户要求清理 DP 子集，见下一节。厂商文件仍保留在内置盘备份，未修改共享区现有文件。
- 本地源码、venv/cache 未迁移；未重新安装、下载、训练或运行策略。本次不是完整 W1 复验，也不是硬件健康认证。
- 下列 80/80 测试、11/11 环境检查和模型指标均为历史 W1 证据；备份和恢复的原始 JSON 内旧路径未改写。早期磁盘失败记录仍在本机 `/home/pan/disk-backups/Expansion-20261024-032725/HANDOFF.zh-CN.md`，未上传；禁止重跑旧磁盘脚本。

## 2026-10-24 本地副本清理

- 用户明确要求删除已经放到外盘的本地 DP 数据副本；这不是删除项目或运行环境。
- 删除前重新读取内外两份数据，41 个文件各 4,295,070,229 bytes 均与原 SHA-256 清单一致，并核对外盘 UUID、文件系统、挂载点及父盘序列号。
- 只删除 `/home/pan/disk-backups/Expansion-20261024-032725/files/dp_week1`；外盘 `/media/pan/Expansion1/dp_week1` 保留。
- 本地源码、`.venv-dp`、厂商文件、原始清单、GPT/EFI 备份和操作记录均保留。当前 DP 数据不再有这份独立内置盘备份。
- 根分区可用空间从 29,090,725,888 bytes 增至 33,385,951,232 bytes，约释放 4 GiB。
- [清理收据](evidence/storage/local-dp-backup-cleanup-20261024.json)为 PASS；资源日志 `outputs/resources/run-20261024T205454-10647.json`，exit 0。
- 原恢复报告的 `backup_retained: true` 是恢复完成时的历史事实；不改写旧报告，当前备份状态以本节为准。本机另有 `/home/pan/disk-backups/Expansion-20261024-032725/BACKUP_STATUS.md`。原清单不再代表内置盘备份目录当前仍包含全部文件。

## 验收表

| 项目 | 状态 | 证据 |
|---|---|---|
| 当前 DP 存储 | 已恢复 | 41/41 文件校验、读写/符号链接、12/12 守卫测试通过；本次未重跑策略 |
| 最终串行复验 | 已通过 | `bash scripts/verify_week1.sh`，exit 0 |
| 测试 | 已通过 | 80/80，`outputs/week1-tests.xml` |
| 环境 | 已通过 | 11/11，`outputs/environment.json` |
| 固定源码 | 已通过 | DP/OpenArm clean；Gym 仅精确 metadata patch |
| OpenArm 基础诊断 | 已通过 | 14/14 gates，0 warning |
| OpenArm 视频 | 已通过 | 30 秒、600 帧、20 fps、640×480 |
| 官方 checkpoint | 已完成 | `4293295813 B`，receipt 与 SHA-256 |
| DP 开发评估 | 已运行 | 3 个种子、各 300 帧，视频有效 |
| 六场景功能诊断 | 已完成 | 2 条完成，4 条未完成 |
| 额外固定诊断 | 已完成 | seeds `10003..10005`，exit 0 |
| 论文性能/跨机器 | 未执行 | 不属于本机 W1 运行验收 |

## OpenArm

- 目录：`/media/pan/Expansion1/dp_week1/outputs/openarm-week1-20261024-005817-+0800/`
- 文件：`report.json`、`openarm-week1.mp4`、`first.png`、`mid.png`、`last.png`、`trace.npz`
- 双臂 joint3 excursion：约 `0.19999 rad`
- 双夹爪 excursion：约 `0.023999 m`
- 自动选择前，运行时将官方 front/side 两个候选相机的 FOV 从 90°调为 35°，最终选择 side camera；相机位姿、dynamics、source 和轨迹不变。
- 这是 OpenArm 基础诊断，不是 learned policy。

## Diffusion Policy

- 目录：`/media/pan/Expansion1/dp_week1/outputs/dp-official-20261024-005826-+0800/`
- checkpoint SHA-256：`bac7221f7e34cd51162dc1972e1a39ffcddc87de1dc1780c44ffa61b88c4ff76`
- SHA-256 provenance：本地计算，非发布者签名。
- 原生 `[512,1024,2048]` 架构、100 DDPM 步、原始 `PushTImageRunner`、`n_envs=1`。

| Seed | Native score | 视频 |
|---|---:|---|
| `10000` | `0` | 300 帧、30 秒、10 fps |
| `10001` | `0.9520230291535516` | 300 帧、30 秒、10 fps |
| `10002` | `0.696411764381858` | 300 帧、30 秒、10 fps |
| `10003` | `0.7105543278081632` | 300 帧、30 秒、10 fps |
| `10004` | `1.0` | 199 帧、19.9 秒、提前完成 |
| `10005` | `1.0` | 235 帧、23.5 秒、提前完成 |

初始批次均值 `0.5494782645118033`，额外批次均值 `0.9035181092693877`。种子 `10004`、`10005` 达到原始 95% coverage 完成条件；其余四条未达到。

两个批次均预先声明，权重和参数不变，无训练、调参或种子筛选。2/6 完成只是开发诊断计数，不是正式成功率、论文 benchmark 或科学性能认证。

## 资源（历史 W1）

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
- 当前无剩余模型任务。DP 数据与存储守卫已恢复，本次没有完整重跑 W1。

## 边界

- 已完成：本机安装、测试、环境、OpenArm 基础诊断、官方 DP 预训练推理可运行性和 2 条功能成功示例。
- 未完成：论文性能复现、科学性能认证、从头训练、六名学生或六台机器验收。
- 未安装或执行：π0/OpenPI、handover、W2/W3 数据或训练、真机、ROS。
- W1 模型工作已关闭，当前 DP 存储恢复已完成。其他工作仍需用户明确批准。
