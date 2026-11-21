# Quest WebXR 遥操作技术说明

更新日期：2026-11-16。早期仿真链路与后续实机/相机接入分别记录，
当前验收状态以 `PROJECT_STATUS.md` 为准。

## 用户操作

### 仿真启动

1. Ubuntu 连接上游网络，同时启动 `pxy111` 并发热点。
2. Quest 连接 `pxy111`，并通过 USB 数据线连接 Ubuntu。
3. 保持 Quest 开发者模式和 USB 调试授权有效。
4. 在 Ubuntu 执行：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_webxr_pedestal.sh
```

5. Ubuntu 图形认证弹窗出现时输入系统密码。脚本通过 ADB 建立：

```text
Quest localhost:8080 -> Ubuntu localhost:8080
```

6. 正常佩戴 Quest，在 Quest 浏览器打开：

```text
http://localhost:8080/
```

7. 页面离开 `preparing` 后点击 `Start`，允许进入混合现实 WebXR 会话。
   裸地址自动跳到内容哈希路径，网页显示 UI 版本；改软件后需重启服务。
8. 左右手柄旁应显示红 X、绿 Y、蓝 Z 坐标轴。桌面 MuJoCo Viewer 中应显示
   V2 pedestal、机器人目标坐标轴和仿真运动。

### 停止

在启动数据流的 Ubuntu 终端按 `Ctrl+C`。不要只关闭 MuJoCo Viewer 后继续保留
WebXR 和 IK 节点。必要时检查：

```bash
ps -ef | grep -E 'dora-openarm-(webxr|mujoco|ik)'
```

### 头显佩戴

WebXR 主线必须正常佩戴头显，不建议挂在脖子上：

- 头显相机负责 Touch Plus 手柄的空间追踪。
- `relative` 模式使用当前头显坐标系作为手柄参考。
- 挂脖子会改变参考姿态，并可能遮挡追踪相机。
- 头显内坐标轴和透视画面只有正常佩戴时才有意义。
- `neck` 模式只补偿正常佩戴时头部绕颈部旋转产生的平移误差。

原生 UDP APK 的上游说明包含挂脖子用法，但它不是当前 WebXR 主线，不能直接
把该操作方式迁移到 WebXR。

### 独立腕部相机预览

这是只读视频入口，不启动 Dora、CAN 或机械臂；运行前仍需新的相机读取授权，
核对身份为 `2bcf:1258`、物理端口正确且未被占用。设备编号和端口可能变化，
不得将示例路径用于身份不符的新设备。一次只开一路，避免继承双流带宽问题。

2026-11-16 实际使用的端口 `2.2` 预览命令：

```bash
ffplay -hide_banner -loglevel warning -nostats \
  -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 \
  -fflags nobuffer -framedrop -an -vf 'hflip,vflip' \
  -window_title 'Waveshare port 2.2 - ROT180 - Q or Esc to close' \
  -x 960 -y 540 -left 60 -top 80 -alwaysontop \
  /dev/v4l/by-path/pci-0000:06:00.4-usb-0:2.2:1.0-video-index0
```

`hflip,vflip` 合起来是旋转 180 度，不是额外镜像；按 Q/Esc 关闭释放相机。
预览不录视频，查看它不能代替帧率、坏帧、时序和完整任务可见性验收。
本轮仍出现 MJPEG APP 字段告警，不以窗口正常显示掩盖告警。
用户确认展开后的当前视野初步可用；后续三相机服务已接入一次 `rot180-v1`，
但真实并发性能、方向和部署一致性尚未验收。
示例程序的单路内核 UVC 取流不继承双腕 libuvc payload=1600 的并发限额。

### 采集和真机入口

采集软件与权限分阶段入口见 `../local/three_camera/README.md`，
任务定义见 `抽屉放方块_数据采集方案.md`；默认用 mock。
逐条操作见 `数据采集操作流程.md`：A 启动不录制，双臂对齐后 X 开始，
再次 X 成功结束，B 优先停臂并将活动条记失败；保存确认后才复位。
真机手册见 `实机_WebXR_操作手册.md`，不是本文仿真启动命令的隐含后续步骤。
官方 UI 启动可使能并移动双臂，不得为了预览相机启动整套实机数据流。

## 数据链路

```text
Quest Browser
  │
  │  HTTP 页面与 /offer 信令
  │  ADB reverse: localhost:8080
  ▼
Ubuntu webxr_http_localhost.py
  │
  ├─ 本地静态前端：WebXR、透视、RGB 坐标轴
  └─ 固定上游 dora-openarm-webxr
        │
        │  WebRTC unreliable "xr" data channel
        │  最新手柄/头显位姿、扳机、握持、按钮
        ▼
    坐标变换、One Euro 滤波、速度限制、0.2 s 位姿超时
        │
        ▼
    dora-openarm-ik
        │  每臂 7 个关节 + 夹爪
        ▼
    dora-openarm-mujoco
        │
        ▼
    OpenArm V2 pedestal Viewer
```

页面和初始 `/offer` 信令经过 ADB reverse。前后端禁用公网 STUN/TURN，只发布
局域网 host ICE 候选；WebRTC 数据通道在 Quest 热点网络内建立连接。

上述图为仿真最小入口。实机使用独立的官方 `dora-openarm` 后端，不让真实命令
先通过 MuJoCo follower；已有官方 Recorder 的本地三相机派生接入，不改上游模型
或电机参数。相机专用服务、Unix socket JPEG 帧、Dora 桥接、起止确认/帧数屏障
和时钟侧车的细节见 `../local/three_camera/README.md`。
软件 mock 完整落盘与独立真实相机预览是两类证据，不等于正式真实任务数据已采集。

## 坐标轴

头显和桌面坐标轴含义不同：

- 头显 RGB 轴由 `local/static/axes.js` 直接使用 WebXR
  `source.targetRaySpace` 绘制，是控制器原始 aim frame。
- 桌面 MuJoCo RGB 轴由 `dora-openarm-mujoco --debug-frames` 绘制，是经过
  WebXR 节点坐标变换后、相对模型 `arm_origin` 的目标 frame。

两者同时观察可以诊断左右交换、轴向翻转、固定旋转和 frame offset，但不能仅凭
外观相似就认定 IK 或真机坐标已经验收。

当前头显坐标轴参数位于 `local/static/axes.js`：

```text
长度：0.16 m
半宽：0.006 m（总厚约 0.012 m）
X：红色
Y：绿色
Z：蓝色
```

坐标轴使用 `source.targetRaySpace`，即控制器 aim frame。改成
`source.gripSpace` 会更贴合控制器握柄，但不再与当前控制目标 frame 完全一致。

## UDP Controller-only

状态：**暂停的实验坑位，不是当前主线。**

不需要头显显示时运行：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_udp_pedestal.sh
```

Pose Transmitter 向 `10.42.0.1:5006` 发送位姿。该模式不需要浏览器或 ADB
reverse。测试中观察到头显位置变化会触发 `OK -> STALE -> INVALID`，之后可
恢复 `OK`。当前头显不适合挂在脖子上，因此停止继续推进；代码和证据保留，等待
稳定固定支架或新的 controller-only 追踪方案。

## IK 与可达性

控制器位姿先转换为 `arm_origin` 下的目标：

```text
[px, py, pz, qw, qx, qy, qz, gripper]
```

WebXR 节点进行参考坐标变换、固定机器人轴映射、frame offset、末端旋转修正、
One Euro 滤波以及线速度/角速度限制。IK 随后：

1. 加载与 Viewer 相同的 V2 pedestal MuJoCo 模型。
2. 为左右 `ee_control_point` 建立相对 `arm_origin` 的 Mink frame task。
3. 等左右两臂都收到新目标后，使用一个共享配置执行双臂 QP。
4. 每次外层更新执行 5 个子步。
5. 应用关节位置、关节速度、近限位制动、奇异位形接近限制、nullspace 回中和
   小量动能正则。
6. 输出右臂和左臂各 7 个关节角加夹爪目标。

当前 IK 的“可达”判断是隐式的：

- QP 完全无解时回滚，并跳过该次输出。
- 目标超出工作空间但 QP 仍有局部可行解时，机械臂可能停在最近可行姿态并留下
  末端误差。
- 当前 Dora IK 节点没有输出位置/姿态残差，也没有明确
  `reachable/unreachable` 状态。
- IK 本身没有环境或双臂碰撞约束；MuJoCo 开启碰撞不等于 IK 已做避碰。

后续应使用 FK 回算得到：

```text
位置残差
姿态角残差
关节限位裕量
奇异值/条件数
自碰撞与环境碰撞
```

再将目标分为可达、受限和不可达，并把结果反馈到头显 HUD。

## 固定模型

IK 和 MuJoCo 明确使用同一个文件：

```text
/home/pan/openarm_diffusion_policy/openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml
```

已检查基线：

```text
nq=18
nv=18
nu=16
ncam=2
nkey=1 (home)
```

不得依赖上游默认的 V2 Cell 模型。

## 仿真最小入口配置

- WebXR 会话：`immersive-ar`
- 显示：现实透视，无机器人相机面板
- 位姿模式：`relative`
- WebXR 发布 tick：100 Hz
- 位姿超时：0.2 秒
- MuJoCo：碰撞开启、执行器控制、30 FPS Viewer
- 离屏相机渲染：关闭
- 数据采集 UI/Recorder：未加载
- 机器人状态快照：未请求
- 该最小入口不加载 CAN、串口或真实机械臂后端；不表示整个项目从未做过实机验证

## 目录

```text
vr_teleoperation/
├── upstream/   固定上游仓库，本地忽略
├── local/      WebXR pedestal 主线代码
├── downloads/  APK 与 Android 工具，本地忽略
├── deps/       可重建构建依赖，本地忽略
└── docs/       状态、技术说明和交接
```

关键文件：

- `local/dataflow_webxr_pedestal.yaml`
- `local/run_webxr_pedestal.sh`
- `local/webxr_http_localhost.py`
- `local/view_webxr_pedestal.yaml`
- `local/static/ar.js`
- `local/static/axes.js`
- `local/static/connection.js`

## 已知限制

- 开发入口依赖 USB 调试和 ADB reverse。
- 浏览器刷新或重新连接前必须保持 ADB 映射。
- 当前只显示手柄轴，不显示完整 OpenArm 三维模型。
- `frame_offset` 尚未针对操作者和工作台完成定量校准。
- 尚未系统验收左右、平移、旋转、夹爪和断流恢复方向。
- `pose-timeout` 只暂停位姿输出，不是真机急停。
- 最小仿真入口不记录真实示范；三相机 mock 落盘和历史真机回读另见状态文档。
- 本轮大疆未恢复可用 UVC 节点，双腕方向仅在独立预览落实，新增外部相机尚未到货。
- 正式多相机同步、完整任务可见性及碰撞/持续受阻/温度保护仍未验收。

## 后续顺序

1. 确认老师要求的是虚实接口闭环，还是仿真策略训练后迁移；不自动引入 Isaac。
2. 保留现有 MuJoCo V2 基线，按确认后的目标推进任务场景与逐轴验收。
3. 新相机到货后核对镜头和机位、左右腕映射、关键阶段视野与预处理版本，
   在单独授权下恢复大疆、验收全组并发与时序。
4. 完成 `PROJECT_STATUS.md` 中的遥操、安全停止、可达性和保护验收，
   再试录少量完整任务示范；不凭预览或训练 loss 宣称真机成功。
