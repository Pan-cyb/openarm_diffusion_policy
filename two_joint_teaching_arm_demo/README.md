# 两关节示教机械臂 MuJoCo 演示

这是一个独立的教学项目，用最小系统串起以下流程：

```text
两关节机械臂建模
  -> MuJoCo 位置控制
  -> 滑条示教与轨迹录制
  -> 轨迹回放
  -> PC 到 MCU 的目标协议
  -> MCU 安全状态机
  -> 两个电机的 CAN 适配边界
```

项目只运行仿真和本机 mock MCU，不会打开真实 `can0`，也不会向实物电机发命令。

## 模型参数

两个关节都使用 OpenArm v1 MuJoCo 模型中 DM4310 类执行器的参数：

| 参数 | 数值 |
|---|---:|
| `damping` | `0.9 N·m·s/rad` |
| `frictionloss` | `0.1 N·m` |
| `armature` | `0.002 kg·m²` |
| `forcerange` | `[-7, 7] N·m` |
| `kp` | `30` |
| `kv` | `1.5` |

来源固定为 Enactic `openarm_mujoco` 提交
`a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c` 中的
`v1/openarm_bimanual.xml`。该上游文件同时说明部分惯量、阻尼和减速箱影响是近似
或尚未确认的，因此这里只用于教学仿真，不是实物标定结果。

本项目的连杆长度、质量和关节范围是为了演示而选取的假设值，见
[`model/two_joint_arm.xml`](model/two_joint_arm.xml)。
为避免把教学重点扩展到自碰撞规划，机器人自身几何碰撞在这个最小模型中关闭。

## 环境

推荐建立独立虚拟环境：

```bash
cd /home/pan/openarm_diffusion_policy/two_joint_teaching_arm_demo
/home/pan/.local/bin/uv venv --python /usr/bin/python3.10
source .venv/bin/activate
/home/pan/.local/bin/uv pip install -e '.[dev]'
```

## 1. 生成示例示教轨迹

先用脚本目标生成一条可重复的示教轨迹，并渲染视频：

```bash
python -m teaching_arm.cli scripted \
  --output outputs/example_demo.npz \
  --video outputs/example_demo.mp4
```

轨迹保存以下数组：

```text
time_seconds  [N]
target_q      [N, 2]  rad
qpos          [N, 2]  rad
qvel          [N, 2]  rad/s
```

## 2. 打开交互式示教

```bash
python -m teaching_arm.cli teach --output outputs/manual_demo.npz
```

打开 MuJoCo 后：

- 展开右侧 `Control`，拖动 `shoulder_ctrl` 和 `elbow_ctrl`。
- 按 `Space` 暂停或继续物理推进。
- 按 `R` 开始记录。
- 按 `S` 停止并保存。
- 按 `P` 回放当前已记录轨迹。
- 按 `H` 回到 `home`。
- 关闭窗口退出。

右侧滑条改变的是位置执行器目标，脚本以 1 kHz 推进物理，并以 100 Hz 记录。

## 3. 回放轨迹

带界面回放：

```bash
python -m teaching_arm.cli replay outputs/manual_demo.npz
```

后台回放并检查跟踪误差：

```bash
python -m teaching_arm.cli replay outputs/example_demo.npz --headless
```

## 4. 模拟 PC 到 MCU

终端 A 启动本机 mock MCU：

```bash
python -m teaching_arm.cli mock-mcu --port 9900
```

终端 B 实时发送轨迹：

```bash
python -m teaching_arm.cli stream outputs/example_demo.npz \
  --host 127.0.0.1 --port 9900
```

发送顺序为：

```text
ARM
  -> TARGET(q1, q2, 最大速度)
  -> ...
  -> DISARM
```

mock MCU 会执行关节范围、速度限制、序号和看门狗检查，然后打印准备发送给
`motor_id=1` 和 `motor_id=2` 的目标。它不会创建 SocketCAN 套接字。

## 5. 固件骨架

[`firmware/`](firmware/) 给出平台无关的 C 语言状态机和 PC 链路协议：

- 上电默认 `DISABLED`。
- 必须先收到 `ARM`。
- 急停、CAN 故障或 PC 指令超时进入 `FAULT`。
- 目标经过关节限位和速度限制。
- 两个电机分别使用发送 ID `0x01/0x02`、反馈 ID `0x11/0x12`。
- 达妙电机实际 CAN 字节打包位于明确的适配边界，当前故意不实现。

在没有核对具体电机固件、控制模式、零位、方向和官方协议前，不得把占位适配器
用于实物。

## 文档

- [从 MuJoCo 到单片机和双电机 CAN](docs/SIM_TO_REAL.md)
- [OpenArm 参数来源记录](docs/OPENARM_PARAMETER_SOURCE.md)
- [MuJoCo Viewer 中文使用手册](docs/MUJOCO_VIEWER_GUIDE.md)
