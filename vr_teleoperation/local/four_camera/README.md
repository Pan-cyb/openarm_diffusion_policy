# 四路微雪采集入口

这是当前四路布局的软件入口，旧 `three_camera/` 保留兼容，不再用旧 DJI
三路真实入口代替四路。正常照明约 30 秒真实图像＋合成关节落盘已通过，
下一步是现场一条完整试采；任务运动画质和真实机器人流程不能由诊断代验。

2026-11-21 正常画面约 30 秒全量落盘及无损旋转、JPEG 哈希读回已通过，
不把它当作任意时长或真机任务通过。最新证据见
`../camera_bandwidth/evidence/2026-11-21-confirmed-light/README.md`。

## 固定采集运行时

四路会话使用 `deps/dora-capture-sdk/python` 中独立的官方 Dora 1.1.0 SDK/CLI，
不覆盖共享 `.venv` 的 1.0.1。新四路会话所有 Dora 节点统一该版本，禁止混用造成
Zenoh 握手错误；旧入口默认不变。`runtime-pins.json` 固定 wheel 和原生库 SHA，
缺失或不符时拒绝静默回退。官方 wheel 留作离线恢复，来源及许可证随包保留。

只给相机数据、采集内部命令/确认以及合成关节诊断配置 backpressure；
真实图中的四个 arm 输入不加反压，运动控制代码、参数和连线不改。
但新中间件仍是新的会话运行环境，**不能把离线录制通过当作真实控制/停止验收**。
首次真机采集前需在新的明确授权下完成既有安全检查及受控小范围复验。

CLI 通过 `dora_capture_cli.py` 调用私有版本，只保留一次现场 `START` 确认；
本轮没有执行真实机器人入口。

真实模式在输入 `START` 前和输入后都会核对当前四路的 `basic_image_ready`。
该门槛约每秒抽检一次原始亮度，拒绝近黑画面或过期检查；真实录制开始及录制期间
也会检查，失效则失败结束。人工 `--confirm-image-quality` 不能绕过它。
这只检查明显黑屏，不判断关键物体可见性、运动模糊、色彩、噪声或曝光同步。
诊断模式仍可保存黑图并明确报告，不能据此开始示范。

## 操作边界

- `--mode mock`：四路合成图像、合成关节，不打开设备。
- `--mode camera-diagnostic --allow-cameras`：四路真实相机、合成关节；
  只用于链路/存储诊断，不加载机器人或 leader，不是训练示范。
- `--mode real`：还需要 `--allow-robot`、操作者、地点，
  相机就绪后只输入一次 `START`，同时确认 CAN/网络准备及机械臂启动。
  本轮没有运行此模式。先现场完成一条完整试采并审片，不直接批量示范。
- 不开机自启、不自动重启主机、不重置集线器、不修改 CAN/驱动/BIOS/供电。
  当前相机与 PCAN 共用集线器，不得用重置集线器恢复相机。

所有命令从 `/home/pan/openarm_diffusion_policy` 运行。使用固定上游环境：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python \
  vr_teleoperation/local/four_camera/launch.py \
  --mode mock --directory /tmp/openarm-four-check --name NEW_MOCK
```

相机专用短诊断：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python \
  vr_teleoperation/local/four_camera/launch.py \
  --mode camera-diagnostic --allow-cameras --max-seconds 30 --name NEW_DIAGNOSTIC
```

`--diagnostic-frames 900 --max-seconds 90` 生成约 30 秒的合成关节源，
`3600 --max-seconds 180` 为约 120 秒。它不是相机帧数，图像按真实异步时间保存；
按实际单条任务长度选择，不把额外 120 秒压测当作首次试采的前置条件。
默认仍为短条。预热须收端确认四个输入且排除在录制外。

默认使用 pkexec 交互授权；`--privilege sudo-n` 只使用当前终端已有 sudo 授权，
不会读取密码文件或保存密码。未授权应失败，不把整套 Python/Dora 作为 root 运行。

诊断数据默认写入已核验 UUID/ext4 的
`/media/pan/Expansion1/openarm_datasets/diagnostics/NEW_DIAGNOSTIC`。
正式示范目标仍为 `openarm_datasets/drawer_block/raw`，不回退系统盘；
拒绝覆盖或自动续写已有数据集，启动配置保存在同盘 `.capture-config/`。

## 录制流程

常用 `bash vr_teleoperation/local/run_webxr_real_pedestal.sh` 已默认转入本四路入口，
只输入一次 `START`。操作者默认当前登录用户名，地点记 `unspecified`，
可用参数或 `OPENARM_OPERATOR`、`OPENARM_LOCATION` 覆盖，不再逐项询问。
自动数据盘/四路/近黑检查仍保留；单次 `START` 不自动标记人工画质验收通过。
`--confirm-image-quality` 仅在确已人工审片时显式使用，不是默认启动必填项。
现有运行进程不会热切换；先按既有安全流程退出旧会话，再重新运行常用脚本。

正式操作仍为：A 启动双臂 → 现场完成 ALIGNED → X 开始本条 → 执行任务 →
再次 X 成功结束 → 等待保存确认 → 复位任务。B 优先停臂，并将本条记失败。
启动、对齐、复位不应进入有效示范；失败原始数据保留，不能当成功条目。
完整现场命令和按键说明见 `../../docs/数据采集操作流程.md`；只写文档命令
不等于本轮获得机器人启动授权。

四路源映射固定为 bus 3：

| 端口 | 角色 | 标准图像 |
| --- | --- | --- |
| 1 | global | 旋转 180° |
| 2.2 | wrist_right | 旋转 180° |
| 2.3 | wrist_left | 旋转 180° |
| 2.4 | chest | 不旋转 |

相机服务在入缓冲前统一方向，三路标 `rot180-v1`，胸部标 `identity-v1`。
不改变左右手身份、动作或机器人坐标系。录制和预览使用同一出口，不能再次旋转。

## 时间与完整性

- `service.py`：单个原生相机进程，有界四路环缓冲；源帧时间、序号保留。
- `drain`：每个连接独立游标，完整读取新帧；慢客户端覆盖报错，不静默丢帧。
- `snapshot`：以最慢一路最新时间为锚，选各路不晚于锚的最近帧；
  默认最大帧龄 200 ms、时间跨度 50 ms。这是主机接收时钟匹配，不是曝光同步。
- `bridge.py`：10 ms 轮询取原始新帧，不用预览降采样代替完整录制；
  记录期间源序号缺口/重置、客户端覆盖或失鲜均阻止成功。
- UI 使用独立小型时间状态输出；空闲/保存时不向 Recorder 灌 JPEG，
  相机服务仍连续取流。新数据用 JPEG 哈希和字节数核对实际保存内容，
  旧数据没有该哈希时会明确区分校验范围。
- 结束时等待各路跨过停止边界，只记录边界内帧，送出实际发布计数；
  Recorder 等齐图像和关节后裁剪边界，fsync 数据/目录/时钟审计，再原子提交 metadata。
- 四路图像各有独立时间戳，不能凭“同是 30 fps”或“都是第 N 帧”认定同步。
  `verify_dataset.py` 给出 20 Hz 因果匹配的帧龄/跨度，不补假帧、不伪造曝光时间。
- UI 继续兼容以 `ceiling` 显示胸部统计，但数据集名称仍是 `chest`；
  全局相机另外纳入开始门控，不能缺路开始。

诊断内容读回：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python \
  vr_teleoperation/local/four_camera/verify_dataset.py \
  /media/pan/Expansion1/openarm_datasets/diagnostics/NEW_DIAGNOSTIC
```

该验证器只接受明确标记合成关节的诊断数据；检查四路 JPEG、方向标签、
源序号、发布/保存集合、起止边界、四组关节各 30 行及时间匹配。
`integrity_pass` 不等于画质通过，黑画面会另行报告，不能用作训练输入。

## 重启与故障

异常退出后先检查进程、四路接口、metadata、partial 和零字节文件，不自动重采：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python \
  vr_teleoperation/local/four_camera/inspect_recovery.py /absolute/dataset/path
```

只读工具不提升未提交条目为成功，不删除/补写文件，不修磁盘。
即使 metadata 有成功标记，也仍需内容读回；整机断电恢复尚未验证。
`native_guard.py` 在服务丢失时保留原生 helper 的管道读端，通知 stdin EOF 后
继续读完收尾，避免权限包装进程先因 SIGPIPE 退出。已测单独服务 SIGKILL 后重开；
直接强杀 helper/guard/整组进程或整机断电不在此保证范围。

主机监视仅在相机模式运行：每 2 秒读 CPU 温度和新内核故障。
当前 6800H 主机的 85°C 仅作日志提醒，不中断或否决数据；95°C 为参考 AMD
Tjmax 设置的软件停止线，不是温控硬件联锁。新内核故障仍会停止，未改风扇、
功耗或固件保护。原死机原因仍未查明，不把普通高占用或一次 85°C 提醒当作根因。

当前实测、失败证据、后续验收条件见：
`../camera_bandwidth/evidence/2026-11-20-commissioning/README.md` 和
`../../docs/四路相机采集验收.md`。
