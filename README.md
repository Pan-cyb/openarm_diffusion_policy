# OpenArm 工作区

本目录现在包含三个用途明确的项目：

```text
openarm_v2/                    OpenArm 2.0 双臂主线
vr_teleoperation/              Quest WebXR 仿真、V2 真机遥操与四路采集
two_joint_teaching_arm_demo/   两关节 MuJoCo 教学实例
```

共享 Python 环境位于根目录的 `.venv-dp/`。它已经成功运行 OpenArm V2
`pedestal` 模型，因此暂时继续复用；后续只有在新的精简环境完成等价验证后，
才考虑删除。

## 快速入口

**新采集视觉 DP：** 11 月 28 日的 CNN / Transformer 批次清单、训练产物、四视角示范起点和两种独立试用命令见[新采集试训](vr_teleoperation/local/dp_pilot/新采集试训.md)。

**采集抽屉示范：** `bash vr_teleoperation/local/run_hand_delta.sh capture`。用 Quest Transmitter 和电脑 8000 页面，不启动 DP、不进入头显 8080。新会话使用加宽起点、原 pedestal PD 和首条位置指令即生效的 100% 模型重力前馈。先看[本次采集交接](vr_teleoperation/docs/handoff/2026-11-28_1656_capture_pdff.md)；输入 `START` 后双臂可能立即运动。

**不用 VR，在电脑上运行 DP：** `bash /home/pan/openarm_diffusion_policy/run_dp.sh`。只运行这一条命令，现场输入 `START` 到预设位，再点击开始 DP。[运动和停止说明](vr_teleoperation/local/dp_pilot/真机部署准备.md)。原 `run_live_verify.sh` 仍只预测、不运动。运动入口仅通过离线测试。

检查 V2 模型：

```bash
cd /home/pan/openarm_diffusion_policy/openarm_v2
../.venv-dp/bin/python scripts/inspect_v2.py
```

打开 V2 普通双臂加底座场景：

```bash
cd /home/pan/openarm_diffusion_policy/openarm_v2
../.venv-dp/bin/python scripts/launch_v2.py
```

启动 Quest WebXR pedestal 仿真遥操作：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_webxr_pedestal.sh
```

保持 Quest USB 调试连接，在 Quest 浏览器打开：

```text
http://localhost:8080/
```

当前四路真实采集入口和单次 `START` 操作见
[`vr_teleoperation/docs/数据采集操作流程.md`](vr_teleoperation/docs/数据采集操作流程.md)。
实机入口会使能机械臂并产生运动，不是普通预览命令；须遵守现场授权与安全边界。
环境、第三方依赖、原始数据、实录图像/日志及本地汇报文件不提交 Git。

运行教学实例测试：

```bash
cd /home/pan/openarm_diffusion_policy/two_joint_teaching_arm_demo
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
  ../.venv-dp/bin/python -m pytest -q
```

旧 Diffusion Policy Week1/V1 工作区已于 2026-11-05 从本地目录移除。其 Git
历史保留在远端提交 `98d1d55`，外盘
`/media/pan/Expansion1/dp_week1` 中的大型 checkpoint 和原始运行数据未修改。
