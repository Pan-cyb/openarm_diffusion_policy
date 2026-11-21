# 2026-11-21 正常照明验收

用户确认照明/镜头无遮挡后恢复。仅相机、合成关节、存储及离线软件测试，
未打开 CAN/串口、使能或移动机械臂，未重启主机、改驱动、接线或供电。

## 最新主线回归

`mainline-rows900-verification.json` 对应
`/media/pan/Expansion1/openarm_datasets/diagnostics/20261121_mainline_rows900`。
本轮只追加这一次约 30 秒实际录制，不继续额外压测或参数搜索。

- 退出码 0，metadata 成功；全局 889 张，胸部/左腕/右腕各 890 张。
  3559 张全部解码、JPEG 哈希、字节数、发布/保存集合和起止边界通过，
  源序号连续，无相邻完全重复图像；四组合成关节各 900 行。
- 四路实测 29.912–29.916 fps，20 Hz 因果匹配最大帧龄 36.04 ms、
  跨度 35.83 ms；这是主机接收时钟匹配，不是曝光同步或真实动作对齐。
- 全局及双腕为 TurboJPEG 无损系数域 180°，胸部保留原 JPEG；
  原生 helper 每帧仅灰度解码校验，输出仍是未经改色的彩色 JPEG。
- 峰值 CPU 80.25°C，syncfs 17.42 秒，八个 UVC 接口和临时参数均恢复。
  日志仍有可丢弃轮询 tick 的调度提醒，不能把它当图像丢失；原始日志保留，
  图像/合成关节是否完整以上述全量读回为准。
- 85°C 规则已改为日志提醒，95°C 保留软件停止；参考当前主机
  [AMD Ryzen 7 6800H 的 Tjmax 规格](https://www.amd.com/en/support/downloads/drivers.html/processors/ryzen/ryzen-6000-series/amd-ryzen-7-6800h.html)。
  不是硬件联锁，未关闭系统温控或修改风扇/功耗设置。
- 先前 `20261121_grayvalidate_light_rows60` 在 85.25°C 被旧规则中断，
  保留失败证据，不将主动停测解释为已经确认主机或相机故障。

下一步是明确授权和现场安全条件下的一条完整 Quest/双臂试采与回放，
不要求先完成 120 秒压力测试或查清所有历史故障。当前诊断不是可训练的任务示范，
也未代验真实控制和 Quest 内实际显示。

## 已确认

- `ae0-5s/report.json`：正常画面四路约 29.91–29.93 fps，无库层序号缺口/坏帧；
  样图预热 3 秒，三路 180°、胸部 0° 的对照图为
  `ae0-5s/confirmed-warm-preview.jpg`，无人工拉亮。
- `manual100-5s`、`manual100-gain128-5s`：临时手动 10 ms 偏暗，gain32→128
  虽读回变化但画面改善很小，拒绝为生产参数，退出恢复原控制。
  当前采用 AE priority0＋自动曝光，白色抽屉的亮部饱和仍需任务区域审片。
- 原始 `20261121_light_rows60` 约 64 MB，逐文件 fsync 收尾 29.28 秒；
  `20261121_light_rows60_syncfs` 经 UUID/ext4 核验、目录树检查后 syncfs，
  同类短条停止到 metadata 提交 1.84 秒。metadata 仍在数据同步之后原子提交。
  此调用同步该 Expansion 卷其他待写数据，不是全系统 sync/修盘/删除。

## 失败与修复

- `mock/rows60`：数据提交后服务退出时报 `terminate called without an active exception`；
  增加源线程收尾等待及启动/关闭互斥，后续正常退出，原失败不删。
- Recorder 原先按桥接 100 Hz 轮询推断相机频率；新文件显式存源名义 30 Hz，
  实际帧率仍按原始时钟核对，不能把 30 配置值当实测。
- `/media/pan/Expansion1/openarm_datasets/diagnostics/20261121_light_rows900_syncfs`
  首次约 30 秒全量条失败：四路各 889 张与发布计数一致，但右臂合成 obs 缺 tick0，
  899/900。严格门控拒绝成功；失败文件、metadata 和日志保留。
- 新诊断源先发录制外预热样本，Recorder 收齐四个输入才确认开始；
  预热样本不会进入条目。`mock/rows900-primed` 是旧 SDK 预热回归，不是可靠传输证明。
- 审查发现 1.0.1 direct 和 daemon 都有可丢弃边界；“强制走旧 daemon”方案被拒绝，
  没有编译或部署自定义 SDK 补丁。报告及来源存于 `../../../../deps/dora-capture-sdk/`。
- 官方 1.1.0 wheel 通过 PyPI SHA-256 后独立装入 deps，不覆盖共享 .venv。
  `mock/sdk110-rows60` 混用版虽内容读回通过，但出现 Zenoh INVALID 握手错误，
  不作为兼容性通过；后改为新四路会话统一 1.1.0。
- `sdk110-unified-proc-maps.json` 核实运行中四节点实际加载私有库，
  `mock/sdk110-unified-rows900` 四路各 892 JPEG、四组关节各 900 行，内容读回通过，
  无连接错误、无图像/关节调度丢弃日志。它是纯 mock，不能冒充物理帧率。

## 正常画面约 30 秒复测

```text
/media/pan/Expansion1/openarm_datasets/diagnostics/20261121_sdk110_light_rows900
```

- `sdk110-light-rows900-verification.json`：全局/右腕各 890 帧，胸部/左腕各 889 帧，
  四路约 29.914–29.916 fps，源序号连续，发布/落盘时间戳集合一致，全部 JPEG 可解码。
- 四组合成关节各 900 行，首行至末行数值和边界核对通过，不是任务示范。
- 20 Hz 因果匹配最大帧龄 36.06 ms、跨度 35.73 ms；主机接收时钟，不是曝光同步。
- 相邻解码图像无完全重复；该检查也不能代替光学时延或同步测试。
- 收尾同步 22.76 秒，停止到 metadata 提交 22.87 秒；八个 UVC 接口和临时控制恢复。
- 峰值 CPU 84.25°C；该次仍使用旧 85°C 保守规则，未进行 120 秒测试。

Expansion 当前是 `usb6/6-1/6-1.3` 的 Seagate Expansion HDD，协商 480 Mbps；
与相机 bus3 不同总线，但写盘通道仍是 USB2。没有拔盘、卸载或改接线。
原主机卡死根因仍未确定，本次短测无新匹配内核硬件错误不等于根因已修。

## 较早无损旋转回归

`lossless-light-rows900-verification.json` 已验证无损旋转及源 JPEG 哈希/字节数：
3557 张，四路约 29.91 fps，四组合成关节各 900 行，全部读回通过。
后续主线复测见本文顶部。原 raw/失败条目不覆盖，新的测试均用新名称。

下载清理已移除独立 SDK staging、旧源码下载和校验辅助；保留已安装 SDK、
发布校验来源、许可证和两只官方 wheel 作为离线部署备份。未删共享环境或外盘数据。
