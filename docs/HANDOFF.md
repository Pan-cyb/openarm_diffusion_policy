# 项目交接

更新日期：2026-10-26

项目路径：`/home/pan/openarm_diffusion_policy`

## 当前结论

这是独立的 Diffusion Policy Week1 项目，只覆盖：

- 官方 image PushT hybrid CNN `train_0` checkpoint 的预训练推理。
- OpenArm MuJoCo v1 的模型加载、关节位置控制、相机和夹爪观测。

项目不包含 π0/OpenPI、handover、W2/W3 数据或训练、learned OpenArm
任务、真机、ROS、硬件操作或系统分区，不得自动进入下一阶段。

GitHub 发布目标为私有 `Pan-cyb/openarm_diffusion_policy`。具体提交使用
`git log -1 --oneline` 核对；远端配置和 `main` 状态分别使用
`git remote -v` 与 `git ls-remote origin refs/heads/main` 核对。本文不固化
可能变化的提交哈希或远端状态。

## 验收与证据

[原始四周任务书](reference/OpenArm_六名学生_四周任务书.pdf)已定位、阅读、
视觉检查并核对 SHA-256：

```text
c8f58f8f2a35ddbc2c9b85a53d640622a72708193432d13211fbb119f540c4ad
```

PDF 的 W1 时间范围为 2026-10-20 至 2026-10-26，内容包括官方 Diffusion
Policy 示例、OpenArm 基础运行和 30 秒视频。

### 2026-10-24 历史 W1

- pytest：80/80。
- 环境检查：11/11。
- OpenArm：14/14 gates，0 MuJoCo warning，30 秒、600 帧、20 fps、
  640x480。
- PushT：固定 6 条开发诊断中 2 条达到原始完成条件、4 条未达到。

PushT `max_reward_score` 是原始 runner 指标，不是二元成功率。2/6 只是相同
权重和参数、无训练、无调参、无种子筛选的功能诊断计数，不是正式成功率、
论文 benchmark 或跨机器结论。

### 2026-10-26 发布检查

- 文件与证据审阅已完成。
- guarded 完整 pytest：81/81 PASS。
- 没有新的 OpenArm 或 PushT 模型运行。
- 没有重跑完整 `bash scripts/verify_week1.sh`。

今日 81/81 只证明当前测试集通过，不改变 2026-10-24 的模型运行指标。

### 仓库内证据

- [Week1 周报](weekly/WEEK1_2026-10-20_2026-10-26.md)
- [发布测试 81/81](evidence/publication/publication-tests-20261026.xml)
- [历史环境报告](evidence/week1/environment.json)
- [历史 Week1 测试](evidence/week1/week1-tests.xml)
- [OpenArm 精选证据](evidence/week1/openarm/)
- [OpenArm 报告](evidence/week1/openarm/report.json)
- [OpenArm 30 秒视频](evidence/week1/openarm/openarm-week1.mp4)
- [PushT 初始批精选证据](evidence/week1/dp-initial/)
- [PushT 初始批报告](evidence/week1/dp-initial/report.json)
- [PushT 初始批原始指标](evidence/week1/dp-initial/eval_log.json)
- [PushT 额外批精选证据](evidence/week1/dp-extra/)
- [PushT 额外批报告](evidence/week1/dp-extra/report.json)
- [PushT 额外批原始指标](evidence/week1/dp-extra/eval_log.json)

精选证据约 1.3 MiB，包含 OpenArm 30 秒 MP4、全部 6 条 PushT 小型视频、
报告、配置和截图。大型模型权重及原始全量输出不纳入仓库。

## 存储与安全

- 大 checkpoint、原始视频和持久运行输出位于外部 ext4：
  `/media/pan/Expansion1/dp_week1`。
- 身份门固定检查 UUID `b8104161-e073-d644-a4b9-f4efbcfc1463`、父盘序列号
  `00000000NT1FVEEZ`、ext4 和挂载点 `/media/pan/Expansion1`。
- `/dev/sda3` 只是曾观察到的设备名，不是持久身份。
- 源码、`upstream/`、`.venv-dp/` 和构建 cache 必须留在本地 ext4。
- 内置盘 DP 数据重复备份已在逐文件哈希核对后按用户要求删除；当前不再有那份
  独立本地 DP 备份。
- 禁止格式化、分区、卸载外盘或运行旧磁盘脚本。

普通负载必须通过 `bash scripts/run-safe.sh`，显式给出本地预算、外盘预算和
timeout。安装脚本必须直接调用；最终串行入口必须使用
`bash scripts/verify_week1.sh`，不能依赖其可执行位。

## Fresh Clone 与恢复

Fresh clone 包含 `docs/evidence/` 中约 1.3 MiB 的精选小型证据和视频，但不包含
`upstream/`、`.venv-dp/`、大型模型权重、外盘 cache 或原始全量输出。

`upstream-pins.json` 与 `scripts/bootstrap_sources.py` 恢复固定上游；
`scripts/install_dp.sh` 重建本地环境。当前安装脚本固定使用
`/home/pan/.local/bin/uv`、`/usr/bin/python3.10` 和仓库内 `.venv-dp`，
不能宣称无需适配即可在任意机器运行。

恢复顺序：

1. 阅读[项目规则](../AGENTS.md)、[技术文档](TECHNICAL_DOCUMENTATION.md)、
   [项目状态](PROJECT_STATUS.md)和[README](../README.md)。
2. 确认目标仍为独立 DP W1；涉及训练、handover、π0、真机或硬件时停止。
3. 只读核验外盘挂载、UUID、ext4、父盘序列号和可用空间。
4. 按真实机器适配 `config/storage.json`，保留等价身份门，不得绕过检查。
5. Fresh clone 才恢复固定上游、本地 venv 和大型外盘 artifact。
6. 安装使用 `bash scripts/install_dp.sh`，不要再套一层 `run-safe.sh`。
7. artifact 和单项负载通过 `run-safe.sh` 执行。
8. 完整复验使用 `bash scripts/verify_week1.sh`，并区分新结果与历史证据。

## 未解决边界

- “六名学生全部运行”仍未完成，不能由当前单机证据替代。
- 尚未完成论文性能复现、正式成功率或跨机器验收。
- OpenArm 仅为基础仿真诊断，不是 learned policy 或 handover。
- W2、训练、数据采集、π0/OpenPI、learned OpenArm、handover 和硬件工作
  均需要用户新的明确人工授权。
