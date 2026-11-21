# Quest 双模式 Pedestal 遥操作

这里保存共享 pedestal IK 和 pedestal MuJoCo 的输入数据流：

- WebXR immersive：当前主线，正常佩戴头显，透视和控制器坐标轴。
- Native UDP controller-only：暂停的实验坑位，保留代码但不作为当前入口。

实现时必须显式向 IK 和 MuJoCo 传入同一个 V2 pedestal XML，不能依赖上游
默认的 Cell 模型。

## WebXR 模式

```text
Quest Browser
  -> HTTP localhost:8080 / ADB reverse / WebRTC
  -> dora-openarm-webxr
  -> dora-openarm-ik
  -> dora-openarm-mujoco (V2 pedestal)
```

第一阶段不加载数据采集 UI、Recorder 或离屏相机渲染。MuJoCo Viewer 开启
`--debug-frames`，用于显示左右手柄目标的红、绿、蓝坐标轴。

## 运行

```bash
bash run_webxr_pedestal.sh
```

## WebXR 实机模式

实机入口只在操作者现场、运动范围清空且硬件空开可触及时运行：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_webxr_real_pedestal.sh
```

当前常用脚本默认进入四路采集，并依次：

1. 检查 Expansion 数据盘并打开四路相机，等待预热和基本画面检查。
2. 要求在终端输入一次 `START`，确认准备和启动双臂。
3. 准备热点、`can0/can1` 的 `1 Mbps / 5 Mbps CAN FD` 及 ADB reverse。
4. 启动四路数据采集与真实机械臂数据流，不再重复询问确认词。

`START` 是运动确认词，不开始录制。系统仍可能为相机和网络/CAN 准备提示管理员授权。

电脑控制 UI：

```text
http://127.0.0.1:8000/
```

Quest WebXR：

```text
http://localhost:8080/
```

停止时回到运行脚本的终端按 `Ctrl+C`，等待左右机器人节点完成 stop 和 disable。
不要把该脚本设置为开机自启。

头显内安全控制：

- 右手 `B`：立即发送 Arm Stop，并停止发送手部 pose。
- 右手 `A`：连续按住 1.5 秒发送 Arm Start。
- 左手 `X`：双臂对齐且输入新鲜后开始录制，再按请求成功结束；等待保存确认后复位。
- 左手 `Y`：在官方初始姿态下，把当前舒适手位映射到 home 末端位置并保存
  共享 frame offset。
- Trigger：只控制夹爪，并在 `STARTED` 阶段许可初始对齐。
- 侧面 Grip：当前不使用。

按 B 停止后，重新摆好双手，长按 A 启动，再分别用 Trigger 完成左右臂对齐。
VR Stop 不能替代硬件空开。

Neutral Capture：

1. Arm Start 后保持双臂在官方初始姿态和 `STARTED`，不要 Trigger 对齐。
2. 把双手放到舒适中立位置。
3. 按一次左手 `Y`；浏览器停止发送 pose。
4. 系统从固定 V2 pedestal 模型的 `home` keyframe 计算左右候选 offset，
   取平均并保存。
5. 长按 `A` 重新执行官方初始姿态，再分别用 Trigger 对齐。
6. 结果写入 `neutral_capture.yaml`，后续启动自动加载。

左右候选相差超过 `0.20 m` 时拒绝保存。失败后保持 pose 暂停，按 `B` 停止。

完整实机操作流程见：

```text
../docs/实机_WebXR_操作手册.md
```

运行脚本会通过图形认证调用 ADB，建立：

```text
Quest localhost:8080 -> Ubuntu localhost:8080
```

保持 USB 调试连接，然后在 Quest 浏览器打开：

```text
http://localhost:8080/
```

浏览器将 localhost 视为安全开发来源，因此可以使用 WebXR，不需要在 Quest
安装本地 CA。看到页面后点击 `Start` 进入 WebXR。

开发阶段前后端都关闭公网 STUN，只使用 Quest 与 Ubuntu 在同一热点上的直连
ICE 候选，避免页面长期停在 `preparing`。

## 当前参数

- 会话：`immersive-ar`
- 头显画面：透视，无相机面板
- 页面入口：ADB reverse 到 `http://localhost:8080`
- WebRTC：仅局域网直连候选，不使用公网 STUN/TURN
- 位姿模式：`relative`
- 位姿超时：`0.2 s`
- 控制更新：仿真入口 `100 Hz`；当前真实采集入口名义 `250 Hz`
- MuJoCo：V2 pedestal、碰撞、位置控制、30 FPS Viewer
- 初始阶段不请求机器人状态快照
- 头显内坐标轴：原始 WebXR 手柄 aim frame 的 RGB 三轴
- 桌面坐标轴：映射到 `arm_origin` 后的机器人目标 RGB 三轴

## UDP Controller-only 模式

状态：**暂停，不继续作为当前采集路线。**

```bash
bash run_udp_pedestal.sh
```

Quest 连接 `pxy111` 热点，打开已安装的 OpenArm Pose Transmitter，目标保持：

```text
10.42.0.1:5006
```

该模式不需要浏览器、证书或 ADB reverse。已验证 UDP receiver、IK 和 pedestal
MuJoCo 可以运行，但当前头显不适合挂在脖子上，因此暂停。只有找到稳定固定支架、
新的 controller-only 追踪方式，或用户明确要求恢复时才继续。

坑位说明见 `../docs/UDP_CONTROLLER_ONLY_TODO.md`。

## 佩戴要求

WebXR 运行时应正常佩戴头显，不建议挂在脖子上。头显相机负责手柄追踪，
`relative` 位姿又使用当前头显坐标系，挂脖子会改变参考姿态、遮挡相机并使透视
可视化失去意义。上游的 `neck` 模式只是补偿头部绕颈部旋转造成的位移，不是
“挂脖子模式”。

## 三相机 USB 带宽诊断

2026-11-15 已验证两只 OV4689 的用户态 UVC 带宽限额绕过方案，原接线下可与大疆
同时收流；后续大疆 H.264/720p/30 加双腕的短时测试本轮无解码错误，
但尚未完成长期数据质量验收，先前失败记录保留。
该工具不访问 CAN，不启动机械臂，不保存图像，也未接入正式 Recorder。
操作、恢复边界和实测证据见 [camera_bandwidth/README.md](camera_bandwidth/README.md)。

2026-11-21 当前四路真实图像和关节数据录制已实际检查；保留旧三路模块作兼容，
不以保存成功代替任务视觉成功或策略部署验收。
当前入口和开始/结束帧检查见 [four_camera/README.md](four_camera/README.md)；
抽屉任务示范方案见 [采集方案](../docs/抽屉放方块_数据采集方案.md)。
