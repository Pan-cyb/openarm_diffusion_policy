# OpenArm V2 主线

本项目用于学习和验证普通 OpenArm 2.0 双臂加底座的 MuJoCo 模型，并为以后接入
Diffusion Policy 建立清晰接口。当前只做仿真，不连接真机。

## 目录

```text
docs/                         状态和官方来源记录
patches/                      Gym 0.21 唯一允许的兼容补丁
scripts/                      V2 模型检查和启动入口
tests/                        无窗口模型回归测试
upstream/diffusion_policy/    固定官方 DP 源码，本地忽略
upstream/gym/                 固定 Gym 源码，本地忽略
vendor/openarm_mujoco/v2/     官方 V2 MuJoCo 模型快照
```

## 环境

暂时复用父目录 `.venv-dp`：

```bash
source ../.venv-dp/bin/activate
python -c "import mujoco; print(mujoco.__version__)"
```

当前环境已验证能够加载和显示 V2 `pedestal` 模型。它还包含完整 DP 依赖，因此
体积较大，但不影响 V2 仿真正确性。

## 检查模型

```bash
../.venv-dp/bin/python scripts/inspect_v2.py
```

期望基线：

```text
nq=18
nv=18
nu=16
ncam=2
nkey=1
```

## 打开 Viewer

动态物理：

```bash
../.venv-dp/bin/python scripts/launch_v2.py
```

只显示 `home`、不推进物理：

```bash
../.venv-dp/bin/python scripts/launch_v2.py --static
```

## 测试

```bash
bash scripts/test.sh
```

测试入口会关闭系统 ROS 安装注入的第三方 pytest 插件自动加载；本项目本身不使用
ROS。

下一步是建立 V2 的关节、`qpos`、执行器和控制范围对照表，不进入真机控制。
