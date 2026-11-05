# OpenArm 工作区

本目录现在只包含两个用途明确的项目：

```text
openarm_v2/                    OpenArm 2.0 双臂主线
two_joint_teaching_arm_demo/   两关节 MuJoCo 教学实例
```

共享 Python 环境位于根目录的 `.venv-dp/`。它已经成功运行 OpenArm V2
`pedestal` 模型，因此暂时继续复用；后续只有在新的精简环境完成等价验证后，
才考虑删除。

## 快速入口

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

运行教学实例测试：

```bash
cd /home/pan/openarm_diffusion_policy/two_joint_teaching_arm_demo
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src \
  ../.venv-dp/bin/python -m pytest -q
```

旧 Diffusion Policy Week1/V1 工作区已于 2026-11-05 从本地目录移除。其 Git
历史保留在远端提交 `98d1d55`，外盘
`/media/pan/Expansion1/dp_week1` 中的大型 checkpoint 和原始运行数据未修改。
