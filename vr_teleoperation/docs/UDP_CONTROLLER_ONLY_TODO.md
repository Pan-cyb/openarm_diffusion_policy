# UDP Controller-only 坑位

状态：2026-11-28 恢复挂脖路线。下文 `neck_sim.py` 仅做输入和模型诊断。
另有显式选择的[四相机手柄位移采集入口](数据采集操作流程.md#手柄相对位移可选待现场验收)，
复用同一手柄映射，尚未现场验收；旧 UDP 数据流不变。

## 挂脖实验：手柄相对起点，头显不是控制原点

在仓库根目录运行（无参数为合成输入，不开网络或硬件）：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python vr_teleoperation/local/neck_sim.py
```

后续仅在明确要测试 Quest 输入时，在同一命令后加 `--listen 10.42.0.1`；
这是电脑热点地址示例，端口为 5006，不能与旧 receiver 同时运行。
使用已有 OpenArm Pose Transmitter，不需要高支架或打开 8080。
站在操作位面朝任务，头显外侧相机朝向双手；先松开 Y，双手同高、等距向前、左右分开，再轻按 Y。
水平的左手到右手方向定义操作者右方，重力竖直方向定义上方，不用头显俯仰角校准。
如果双手交叉或一前一后，前进方向就会标错。先只移动一只手核对电脑显示的 `delta_mm`。
第一次标定对齐模型 home；后续标定接续冻结前的模型目标，不代表实际机械臂状态。

程序直接复用仓库固定的 Enactic `dora_openarm_vr.quest_receiver` 的 Unity 左右手系转换、
机器人轴映射和姿态编码，以及 OpenArm `Kinematics` 的 Mink/DAQP 与 V2 pedestal 模型。
发送器必须提供房间坐标 `lc/rc`、源时间 `t`、有效标志 `v/vl/vr` 和 `y/b`；
不使用逐帧 `rf`。相对位姿思路也见 DROID 操作手册，但未移植其夹持按键或控制器。
依据：上游 `nodes/dora-openarm-vr/README.md`、`quest_receiver.py`；
https://droid-dataset.github.io/droid/example-workflows/teleoperation.html 。

电脑显示模拟目标位移、IK 后的 FK 位置和角度残差；不是实测机器人误差。
实验中 B、非 OK 包、断流/源时间缺口达 0.20 秒、明显跳变（单帧超过 10 cm 或 45°）
都会撤销对齐，恢复后须松开再按 Y。这些是诊断门槛，不是物理安全阈值。
APK 协议没有单独的重定位事件，小幅或缓慢的参考系漂移不能自动识别；
主动重新居中前先 B，之后重新对齐。此入口不处理夹爪、不写训练数据。

挂脖不是已验收用法。后续首先只看输入：确认不会休眠，双手静止时轻动头显，
再做完整拉抽屉和放块动作，观察遮挡、掉帧以及重新对齐；不要用真机验证这些条件。
下文为 2026-11-07 的旧路线记录。

## 已有证据

- OpenArm Pose Transmitter 补丁版默认发送到 `10.42.0.1:5006`。
- Quest 到 Ubuntu 原始 UDP 曾测得约 72 Hz。
- `dataflow_udp_pedestal.yaml` 已通过 Dora 校验。
- Quest receiver、双臂 IK 和正确 V2 pedestal MuJoCo 已同时运行。
- MuJoCo 中可看到映射后的左右目标坐标轴。
- 测试过程中观察到 `OK`、`STALE`、`INVALID` 追踪状态及恢复。

## 暂停原因

- 当前头显不适合稳定挂在脖子上。
- controller-only 仍依赖头显作为追踪基站。
- 头显位置变化会改变参考坐标并可能遮挡追踪相机。
- 当前 receiver 在完全断流时仍可能保留最后缓存，真机前缺少独立 watchdog。

## 保留文件

```text
local/dataflow_udp_pedestal.yaml
local/run_udp_pedestal.sh
downloads/OpenArm_Pose_Transmitter_0.1.0_default-10.42.0.1-patched.apk
```

## 恢复条件

满足以下任一条件后再恢复：

1. 有稳定固定支架，并验证头显相机能覆盖完整手柄工作区。
2. 找到不依赖头显佩戴的稳定 controller-only 追踪方案。
3. 用户明确要求重新研究挂脖子路线。

恢复后仍需先在 MuJoCo 验证追踪丢失、坐标跳变、漂移和断流行为，不直接接真机。
