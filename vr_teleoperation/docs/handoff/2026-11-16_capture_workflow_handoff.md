# 2026-11-16 采集流程软件交接

## Current Goal

等待用户准备的新相机到货，先完善默认 8080 前端和受保护的逐条采集流程。
完整操作入口为 `../数据采集操作流程.md`，状态见 `../PROJECT_STATUS.md`。

## Important Context

- 范围限 `vr_teleoperation/`；不修改固定 upstream、V2 vendor 或其他独立项目。
- 本轮未开真实相机、CAN/串口，未发机器人命令；后续仍需新的现场授权。
- 新增全局相机尚未接入，双腕物理角色仍为空，大疆识别尚需恢复。
- 工作树原本有根规则/README/.gitignore 修改、未跟踪报告和整个 VR 项目；
  不回滚、不暂存、不提交这些既有工作。

## Completed This Session

- 默认 8080 跳内容哈希路径，版本显示、所有静态模块同前缀和 no-store；
  旧 query 也进入同一版本，头显已缓存网页仍需实际重载验收。
- 官方 UI/Recorder 本地适配：统一 VR/桌面状态，双臂 ALIGNED/epoch/新鲜输入门控，
  X 开始/成功，B 停臂/失败，A 启动不录制；保存确认前不接新条。
- 起止源时间边界过滤、四路关节流与图像尾帧检查、取消保留失败。
- Expansion UUID/path/容量检查，默认原始数据目录已配置，未写真实数据盘。
- 腕部单次 `rot180-v1` 接入统一相机出口，保留时间戳和变换说明。
- 更新规则、操作流程、技术说明、任务方案、状态和最新交接入口。

## Changed Files

- `local/webxr_http_localhost.py`、`local/static/{index.html,ar.js,safety-controls.js}`。
- `local/dataflow_webxr_real_three_camera.yaml`、`local/three_camera/` 控制/存储/方向、
  UI/Recorder/相机适配、mock 验证与测试。
- `local/test_webxr_http.py`、`local/test_vr_controls.mjs`。
- `AGENTS.md`、`docs/` 中本次流程与关联状态/手册；未修改历史交接文件。

## Verification

- Python 离线单测、HTTP 回归、实际前端按钮处理的 Node 离线测试。
- 真实 Recorder 配合三路合成相机及双臂合成数组端到端落盘，检查时间边界、
  方向标记和图像/侧车集合；合成数据不是实机证据。
- Dora YAML validate（仅描述符检查，不启动驱动）。
- 临时 8080 仅运行静态/HTTP FastAPI，不启动 Dora 或机器人；用本地无头浏览器
  检查默认 URL 和 HUD canvas，不替代 Quest 内视觉/按键验收。
- 最终本轮摘要见 `../../local/three_camera/evidence/2026-11-16/workflow-verification.json`。
  临时服务、合成数据和 scratch 最终清理，源代码、摘要与必要日志保留。

## Known Issues

- Quest 内实际更新、双臂控制/方向/停止、Neutral Capture 均需现场验收。
- 数据集只支持当前三相机；新全局视角、真实同步/延迟和图像方向性能未验收。
- 默认旧遥操图不是新采集入口；X 在那里禁用，不绕过 `launch.py --mode real`。
- 原桌面 Collecting 消失不等于已保存，必须等 capture/status 或 HUD 的 IDLE/Saved。
- 断流门控用于结束数据，未实现硬件 deadman、碰撞/受阻/温度停机保护。
- 进程崩溃或盘满仍可能留不完整文件，不能承诺断电数据完整。

## Next Recommended Steps

1. 到货后只在明确相机授权下核对身份、左右角色、关键阶段构图和全组负载。
2. 在明确运动授权、清空工作区和可触硬件空开的前提下验收 Quest 新页面与双臂对齐。
3. 按新流程先试录 5–10 条、逐条回放/质检，不直接批量采集或启动训练。

## Resume Instructions

先读 `../../AGENTS.md`、`../PROJECT_STATUS.md` 和本次流程手册。
所有真实入口命令仅为未来现场用法，不因本次软件准备完成自动运行。
