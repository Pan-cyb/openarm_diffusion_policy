# 2026-11-20 夜间四路验收证据

所有真实测试只开相机，未打开 CAN/串口或控制机器人，未自动重启主机、
重置集线器、修盘或更改驱动/BIOS。主线目标仍未完成，正常照明确认待用户回复。

## 文件索引

- `controls-port-*.json`：先前 V4L2 只读控制查询；可能是驱动缓存值。
- `ae0-5s/report.json`：720p/30/payload1280，临时 AE priority=0；
  稳态四路 29.887–29.930 fps，无源序号缺口，图像接近全黑。
- `manual100-5s/report.json`：10 ms、gain32、AE priority0，
  稳态约 29.97–30.01 fps，无缺口，仍近黑；不是选定曝光方案。
- `original-ae1-dark-5s/report.json`：原开关=1 对照，四路约 9.98–10.00 fps；
  原始样图和日志保留。不得用改变后的 scene/压缩大小作正常带宽结论。
- `four-pipeline-dark-verification.json`：首次真实四路＋合成关节 Recorder 读回，
  四路各 30 JPEG，完整性通过，图像质量未通过。
- `service-restart-dark/`：首次强制终止服务，八个 UVC 接口未恢复，失败保留。
- `service-restart-hup-dark/`：仅加 SIGHUP 处理仍失败，失败保留。
- `service-restart-exittrace-dark/`：失败复测，收集到后代退出码 -13（SIGPIPE）
  与 1；它定位权限/管道关闭问题，不说明主机硬件卡死根因。
- `service-restart-guard-dark/`：加入 native_guard 后，服务 SIGKILL、
  全部相机接口恢复、四路重新就绪、干净停止的实测通过。
  仅相机服务进程，不是主机重启或断电恢复。
- `restore-original-ae1.json`：明确恢复试验前 AE priority=1；
  通过反向再目标写入避免 V4L2 缓存与 libuvc 实际控制不一致。
- `four-pipeline-guard-dark-verification.json`：最终 guard 生产链路真实四路诊断读回。

## 最后一次真实落盘

Expansion 数据（不要删除/覆盖）：

```text
/media/pan/Expansion1/openarm_datasets/diagnostics/20261120_four_pipeline_guard_dark
/media/pan/Expansion1/openarm_datasets/diagnostics/.capture-config/20261120_four_pipeline_guard_dark
```

全局 31 帧、胸部/左右腕各 30 帧，图像各路实际约 29.96–30.00 fps；
源序号连续，文件集合等于发布集合，所有 JPEG 720p 可解码；
全局和腕部 rot180-v1，胸部 identity-v1。四组关节各 30 行，明确合成，
任务 prompt 和 metadata 标记“不是示范”，不得混入训练数据。
20 Hz 因果时间网格最大帧龄 31.99 ms、四路跨度 31.06 ms，
均为主机接收时钟，未验证曝光同步。

最终 `camera-service.log` 中四路均为：
`before ae_priority=1` → `applied=0` → `restored=1`，
原生各路 bad=0，`camera_restore.ok=true`。完整日志在同盘 provenance 目录。
期间 CPU 约 65–74°C，没有命中 85°C 保守停测线；本轮未见新匹配内核硬件错误。
这不是主机稳定性根因结论。

## 离线与失败保留

后续近黑门控更新：新增 9 项回归，four_camera 共 37 项通过，连同此前未改动的
73+30 项，当前累计 140 项 Python。`saved-dark-image-gate.json` 离线读回 121 张
已保存真实图像，原时间戳满足约 30 fps，但四路亮度约 2/255，被正式入口门槛拒绝。
回放禁用预热等待以测试保存像素门槛，不是新开设备或运行性能/预热验收。
`mock-image-gate-verification.json` 为新版完整合成 Recorder 读回，四路各 31 JPEG、
四组关节各 30 行。当前真实图像检查新增开销还需正常场景实测。

73 项 three_camera、30 项 camera_bandwidth、28 项 four_camera Python 测试通过，
另有 6 组原生帧传输/生命周期离线测试通过。真实数据流只执行 Dora validate，
没有启动真实机器人数据流。
两次纯 mock Recorder 在 `mock-runs/`；首次胸部缺显式方向标签的核对问题已保留，
后一次 identity 标记和四路内容校验通过，不拿合成帧率作真实验收。

本轮没有新增下载/安装包。独立 worker 临时源码在合并检查后清理；
保留 active helper/libuvc 构建、全部失败/诊断数据、日志与原旧 0 字节 JPEG。

## 下一步

先确认现场照明或遮挡，正常照明下审查任务区域和曝光、运动模糊，再逐级短测、
30 秒和 120 秒。当前四路均近黑，绝不能标记“可开始真实示范采集”。
此前整机卡死原因、断电恢复、真实关节与相机同步、Quest 实际头显流程仍待独立验收。
