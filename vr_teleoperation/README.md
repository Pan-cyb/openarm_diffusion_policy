# Quest 3 WebXR 遥操作与四路采集

仿真入口：

```text
Quest Browser -> ADB reverse -> WebRTC -> IK -> V2 pedestal MuJoCo
```

真实采集主线：

```text
Quest Browser -> WebRTC -> IK -> 官方 V2 pedestal 驱动
四路微雪相机 + 双臂反馈/目标 -> Recorder -> Expansion 原始数据
```

常用 `local/run_webxr_real_pedestal.sh` 默认进入四路采集，自动检查相机/数据盘，
只输入一次现场 `START` 才准备并启动机械臂。对齐后 X 录制/成功结束，B 停臂/失败。
具体入口、保存位置和状态说明以
[`docs/数据采集操作流程.md`](docs/数据采集操作流程.md) 为准。

暂停的实验坑位：

```text
Pose Transmitter -> UDP 5006 -> IK -> V2 pedestal MuJoCo
```

## 当前边界

- 使用普通 OpenArm 2.0 双臂加底座模型。
- 仿真与真机证据分开；不因代码就绪自动连接、使能或移动真实机械臂。
- 上游 Dora 数据采集仓库只作为固定依赖和实现参考。
- 初始最小数据流不加载采集 UI、Recorder 或 Cell 的五路相机。
- WebXR 用于正常佩戴、透视和头显内控制器坐标轴。
- UDP controller-only 已完成仿真链路验证，但因当前头显不适合挂在脖子上而暂停。
- 开发阶段保持 Quest USB 调试连接，通过 ADB reverse 避免本地 CA。

## 目录

```text
upstream/   固定第三方仓库，本地忽略
local/      本项目自己的配置与测试
downloads/  APK 和 Android 工具，本地忽略
deps/       可重建的构建依赖，本地忽略
```

本仓库只提交源码、测试、配置和文档。虚拟环境、第三方源码/构建物、SDK wheel、
原始图像/Parquet、运行日志、现场标定和汇报产物保留本地，不随 Git 推送；
`evidence/` 仅保留 README 索引，实录文件引用可能只在原采集机器上可用。
相机辅助程序构建说明见 `local/camera_bandwidth/README.md`，
私有目录中的官方固定运行时说明见 `local/four_camera/README.md`。
全新检出还需按上述说明准备固定依赖，本仓库不包含已安装环境或模型训练权重。
第三方来源、许可和本地改编说明见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

当前固定上游提交：

```text
enactic/dora-openarm-data-collection
5be94fe9873b02d5123bedc96e07272ac0112c2d
```

主线入口见 [`local/README.md`](local/README.md)。

详细操作与架构见 [`docs/WEBXR_TELEOPERATION.md`](docs/WEBXR_TELEOPERATION.md)。

实机启动、对齐、夹爪、复位和急停流程见
[`docs/实机_WebXR_操作手册.md`](docs/实机_WebXR_操作手册.md)。
