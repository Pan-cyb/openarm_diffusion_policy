# OV4689 多相机带宽诊断

## 当前现场：2026-11-20

已安装四路微雪。用户确认 `2.2`=右腕、`2.3`=左腕、`2.4`=胸部、`1`=全局；
左右腕与全局旋转 180°，胸部不旋转。配置见 `four_camera_layout.json`。
当前四路都在 bus 3；1600 四路启动失败，1280 可短时并发但帧率不齐且有序号缺口。
主机异常重启后，落盘 511 张中一张变为 0 字节，**没有通过正式采集验收**。
完整结果与预览见 `evidence/2026-11-20-four-camera/README.md`。

`uvc_pair_probe` 新增末尾 `--ports 1,2.2,2.3,2.4`，不传仍只开原双腕；
还支持 `--restore-only --ports ...`。仅允许这四个已核实端口，不能随意推广到其它拓扑。
`check_four_cameras.py` 和 `run_four_camera_probe.py` 会开真实相机，必须另有当次授权；
`verify_four_camera_disk.py` 仅读已保存文件，不打开设备。
重启之后没有再次开流；不要自动重复测试、运行磁盘修复或压力测试。

以下为旧双腕/DJI 的历史方案，不代表本次四路布局已迁入正式 Recorder。

验证日期：2026-11-15。适用于本机当前 USB 拓扑，不是训练采集入口。

## 已确认与未完成

- 电脑：ASUS FA507RM，Ubuntu 内核 `6.8.0-106-generic`。
- 两只微雪申报 `2bcf:1258`、UVC 1.5，相同序列号，位于 USB bus 3 的
  `2.2` / `2.3` 端口；它们与 DJI Osmo Action 5 Pro、PCAN 共用一个 Hub。
- 原始 720p/30 fps MJPEG 请求均为每微帧 3060 字节，第二路启动失败。
  原值用户态复现见 `evidence/2026-11-15/baseline-libuvc009.*`。
- 只在本次用户态取流中，把两只微雪的
  `dwMaxPayloadTransferSize` 改为 1600；实际选择从 altsetting 11 降为 8。
- 双微雪 720p MJPEG 跑通；加入大疆后仍可同时收流。
  两轮腕部各 60 秒，分别约 29.92 / 29.88 fps，所有交付帧解码成功。
- 大疆 H.264/25 fps 有启动参数集与输出时间戳告警；
  MJPEG/30 fps 约 29.62 fps，但出现坏帧/残帧告警。
  这些证据都保留，不能标为“三路无错误验收通过”。
- 后续针对大疆查阅官方手册并做带时间的对照后，找到短时通过的候选组合：
  大疆先启动 H.264/720p/30，双腕 MJPEG/720p/30、payload=1600。
  大疆 55 秒、双腕重叠约 35 秒，三路本轮均无解码告警/失败；
  尚非长期验收。MJPEG 对照曾导致一只腕部坏帧，失败证据未删除。
  详细参数、手册与诊断入口见 [DJI_WEBCAM.md](DJI_WEBCAM.md)。
- MJPEG 三路测试的进程重叠时间约 43 秒，不是三路完整跑满 60 秒。
  原始日志和重叠窗口统计见 `evidence/2026-11-15/summary.json`。
- 未测试长期运行、运动场景压缩峰值、Quest 流量、CAN 通信负载或时间同步。
  零库层序号缺口不等于证明传感器/USB 链路没有任何丢帧。

这说明存在不换线的用户态绕过路径，但不是系统范围永久修复。
普通 FFmpeg/OpenCV 仍使用原来的内核驱动协商，不能默认继承这里的限额。
未来正式采集若采用该方案，需要显式接入同样的 UVC 后端；本次未改动
遥操数据流、训练入口或官方 Recorder。

## 范围和恢复

程序只打开 bus 3、端口 `2.2` / `2.3`、VID:PID 为 `2bcf:1258` 的相机，
按物理端口重新查找地址，不依赖重复的序列号或 `/dev/videoN` 顺序。
端口不符则拒绝取流。

执行前仍需得到摄像头取流授权。程序会临时解除这两只相机的 UVC 接口绑定，
通过 libuvc 取流，正常退出或收到 SIGINT/SIGTERM 后请求交还内核驱动。
后续测试发现 libuvc 有一次未自动交还一只相机，因此现在退出时会额外核对两个接口；
若控制接口未绑定，仅对核对过身份的该相机尝试定向绑定。
不解除其它驱动、不重置 Hub；恢复失败会明确返回非零状态，不能算完整成功。
不打开音频接口，不访问 CAN，不启动机械臂，不保存图像；
默认诊断模式中，压缩帧和解码后的 RGB 仅在内存中使用，stdout 仅输出元数据 JSONL。
不要与其它正在使用这两只相机的程序并发运行；启动前先确认设备未被占用。

系统 `uvcvideo.ko` 未替换，`quirks=4294967295`（原始自动选择值）及 `trace=0`
未修改，没有写入 modprobe/udev 配置或刷固件。每次退出后仍应检查绑定和节点。
不要用 `kill -9` 作为正常退出方式；异常强杀后不保证接口自动恢复。
若需恢复，只处理这两只相机，不要重置带 PCAN 的整个 Hub。
`uvc_pair_probe --restore-only` 仅核对并在必要时尝试定向恢复两只微雪，不打开视频流；
需要写入绑定时仍需管理员权限。该恢复路径不保证能处理设备拔出或其它驱动占用。

## 构建与授权运行

本地依赖位于 `../../deps/libuvc-0.0.9`，对应官方仓库：

```text
https://github.com/libuvc/libuvc
tag: v0.0.9
commit: 448d78b9aa4f54172c1ca5b5d1b4a6b9b93f7957
```

通过官方 HTTPS 仓库取得固定提交，Git 对象检查通过，未修改上游源码。
该标签是轻量标签，没有签名标签；固定提交和 `git fsck` 仅记录来源及对象一致性，
不等同于通过发布者签名或独立可信校验和验证。没有安装系统软件包。
最初尝试的 libuvc 0.0.7 不支持本相机 UVC 1.5，在取流前失败，日志保留，
无用的旧源码和构建目录已清理。

构建命令不打开任何设备：

```bash
cd /home/pan/openarm_diffusion_policy
bash vr_teleoperation/local/camera_bandwidth/build_probe.sh
python3 -m unittest discover \
  -s vr_teleoperation/local/camera_bandwidth -p 'test_*.py' -v
```

已授权取流时，双微雪诊断示例（仍会真实打开相机）：

```bash
pkexec /usr/bin/timeout --signal=TERM --kill-after=5s 80s \
  /home/pan/openarm_diffusion_policy/vr_teleoperation/deps/camera-bandwidth-build/uvc_pair_probe \
  60 1280 720 1600 > /tmp/ov4689-diagnostic.jsonl

python3 vr_teleoperation/local/camera_bandwidth/summarize_camera_log.py \
  /tmp/ov4689-diagnostic.jsonl --pretty
```

参数依次为持续秒数、宽、高、每微帧 payload。最后一项为 `0` 时保留相机
原始请求，仅用于对照。不要直接把已验证的 720p/30 fps、1600 参数推广到
其它分辨率、帧率或其它相机。程序退出后限额不留在系统驱动中。

## 2026-11-16 采集服务扩展

新增末尾 `--frames` 模式，供 `../three_camera/service.py` 专用：
stdout 此时是带 OAC1/长度/JSON 时间信息的真实 JPEG 二进制流，不再是 JSONL。
不要把该模式输出重定向成“仅元数据诊断日志”。特权辅助程序自身不落盘图像，
普通 Recorder 在操作者开始 episode 后才保存图像。

此模式仍需明确摄像头取流授权，最长允许 10800 秒；普通诊断仍限制 90 秒。
服务通过关闭 stdin 请求正常退出，程序在枚举前和写入背压时都检查 EOF，
避免认证延迟或父进程结束后继续占用相机。写入超时、坏帧和恢复失败会报错退出。

新增原生测试覆盖帧格式、背压下父进程退出、EPIPE、坏帧优先级、写入超时和部分写入；
均使用 mock 载荷/管道，不访问 USB。新的二进制路径尚未做真实设备验收，
不能把以前的短时诊断结果当成完整采集后端验收。

大疆并行测试命令和完整错误输出保存在 `*-dji.json` 的 `command` / `stderr`。
其中 `-enc_time_base 1:1000000` 只避免输出端把原始时间戳量化到粗帧周期，
不是三相机同步或时间校准。

## 资料

- 微雪说明：https://docs.waveshare.net/OV4689-4MP-USB-Camera-A/
- Linux UVC FAQ：https://ideasonboard.org/uvc/faq/
- Linux 6.8 `uvc_video.c`：
  https://github.com/torvalds/linux/blob/v6.8/drivers/media/usb/uvc/uvc_video.c
- libuvc 固定版本流控制实现：
  https://github.com/libuvc/libuvc/blob/v0.0.9/src/stream.c

Linux 6.8 的 `FIX_BANDWIDTH` 仅重新估算未压缩格式，不能直接修复 MJPEG 的
过大预留。本工具是在用户态显式选择较低的流带宽档位，再检查真实交付和解码。
