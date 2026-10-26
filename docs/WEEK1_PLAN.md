# Week1 计划

周期：2026-10-20 至 2026-10-26

状态：**已关闭：本机运行验收完成，论文性能与六人跨机器验收未完成**

## 完成清单

- [x] 固定并校验 Diffusion Policy、OpenArm MuJoCo 和 Gym 提交。
- [x] 启用 8 GiB memory、512 MiB swap、4 CPU affinity、磁盘余量与单任务锁守卫。
- [x] 在本机 ext4 创建 `.venv-dp` 并安装固定依赖。
- [x] 完成环境 11/11 检查和 105 个 distributions 元数据记录。
- [x] 完成 pytest 80/80，保存 `outputs/week1-tests.xml`。
- [x] 下载并校验 `4293295813 B` 官方 PushT checkpoint。
- [x] 保持原生 `[512,1024,2048]` 架构和 100 DDPM 步。
- [x] 完成 OpenArm v1 模型加载、关节控制、相机与夹爪观测。
- [x] 完成 30 秒、600 帧、20 fps、640×480 OpenArm 最终视频。
- [x] 完成 DP 单环境 3 个开发种子、各 300 帧评估与视频验证。
- [x] 运行 `bash scripts/verify_week1.sh`，退出码 0。
- [x] 完成额外固定诊断 seeds `10003..10005`；相同权重和参数，无训练、调参或种子筛选。

## 实际输出

- OpenArm：`/media/pan/Expansion/dp_week1/outputs/openarm-week1-20261024-005817-+0800/`
- DP：`/media/pan/Expansion/dp_week1/outputs/dp-official-20261024-005826-+0800/`
- 环境：`outputs/environment.json`
- 测试：`outputs/week1-tests.xml`
- 汇总：`docs/WEEK1_REPORT.md`
- 额外诊断：`/media/pan/Expansion/dp_week1/outputs/dp-official-extra-20261024-011500/`

## 验收解释

- OpenArm 14/14 gates 通过，0 warning；自动选择前将两个官方候选相机的运行时 FOV 从 90°改为 35°，最终选择 side camera，只改善视图，不修改相机位姿、dynamics、source 或轨迹。
- 六个开发场景中，`10004` 和 `10005` 达到原始任务完成条件并提前终止，其余四条未完成。
- 两批均为预先声明的固定种子，权重和参数不变，无训练、调参或种子筛选。
- 本周形成了本机可运行闭环和 2 条功能成功示例，但 2/6 不是正式成功率，也不是论文基准或科学性能认证。
- 仅验证当前笔记本，不扩展为六名学生或六台机器验收。
- 额外批次固定使用相同权重和参数，不调参、不筛选种子，仅补充 W1 诊断。

## 历史问题

- uv/exFAT symlink 失败：改用 ext4 cache，无全局系统修改。
- 下载慢速、中断和 range underflow：保留连续 partial 与失败日志，最终由有界重试恢复。
- OpenArm CLI duration bug：已修复并加入回归测试。
- 早期 90° FOV 视频：保留为历史证据；最终视频使用运行时 FOV 35。

## 后续边界

W1 到此关闭。不得自动启动 W2、W3、数据采集、训练、π0、handover 或硬件工作；任何下一目标必须由用户明确批准。
