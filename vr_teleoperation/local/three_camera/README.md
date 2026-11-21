# 三相机采集接入

**2026-11-20 现场已变更**：当前为四路微雪，`2.2` 右腕、`2.3` 左腕、
`2.4` 胸部、`1` 全局；双腕和全局旋转 180°。本目录仍是旧 DJI 胸部三路后端，
尚未迁移四路，不要直接运行这里的真实入口。当前诊断结果和主机重启/落盘失败见
`../camera_bandwidth/evidence/2026-11-20-four-camera/README.md`。

日期：2026-11-16。默认离线 mock；本次接入未打开真实相机、CAN 或机械臂。

2026-11-20 保存补强：原子发布 JPEG，收尾同步本条图像/Parquet 和目录，
metadata 原子提交后才回传确认；写入/同步失败不发 `ended:success`。
本采集契约要求原始时钟侧车存在、非空且已同步，缺失或同步失败也不能确认成功。
流内延后 fsync，避免每帧 HDD 同步阻塞；CLOSING 执行集中持久化屏障。
仅 Recorder 订阅使用有界缓冲，不对机器人控制流反压；mock 的已知发送计数不足时明确失败。
这没有将三路生产连线迁移成四路，真实吞吐/重启恢复仍未验收。

完整按键、起止边界、Expansion 路径和逐条操作见
`../../docs/数据采集操作流程.md`。后续真实设备部分是未来用法，不是当前授权。

## 当前完成范围

- 独立相机服务：胸部 DJI 用已测试的 H.264/720p/30 参数，
  双腕用 libuvc/MJPEG/720p/30、payload=1600；先启动胸部再启动双腕。
- 腕部原生辅助程序新增二进制帧输出，JPEG 仅经内存/管道传送，不在特权进程落盘。
- 胸部 H.264 解码后转为 JPEG，经带 PTS 的 NUT 管道传送，
  以适配官方 Recorder。新增的这段真实设备路径尚未做实机验收。
- Dora 桥接输出 `camera_chest`、`camera_wrist_left`、`camera_wrist_right`。
  UI 的第三路统计暂用原 `camera_ceiling` 槽位，但数据集明确保存为 `chest`，
  不是新增了一台天花板相机，也没有把单目伪装成双目。
- 复用固定上游 Recorder 的 main/EpisodeWriter/DatasetWriter，
  不改上游源码；本地适配层增加开始确认和结束帧数检查。
- `ui_adapter.py` 在固定官方 UI 外增加统一录制门控；X 开始/成功结束，
  B 停臂/失败。双臂 ALIGNED、epoch 匹配、反馈/VR/三相机均需在 1 秒内；
  录制外默认不创建 episode。结束确认前拒绝开始下一条。
- 腕部在相机服务统一入口应用一次 `rot180-v1`（JPEG 解码/旋转/重编码，
  quality=95），预览和保存共用；胸部不旋转。合成非对称图离线检查通过，
  正式设备性能/方向与未来部署仍需验收。
- 未接通模型训练/自主运动，未采集真实示范；不能用 mock 作为物理验收证据。

原 `run_webxr_real_pedestal.sh` 默认仍用原数据流，只有显式传入
`OPENARM_REAL_DATAFLOW` 才会选择派生入口。不要直接运行实机 YAML 绕过启动确认，
官方 UI 启动会执行 Arm Start。

## 离线验证

下面仅产生合成 JPEG 和合成关节数组，不导入/启动机器人驱动，不请求管理员权限：

```bash
cd /home/pan/openarm_diffusion_policy
PY=vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python
"$PY" -m unittest discover -s vr_teleoperation/local/three_camera -p 'test_*.py' -v
"$PY" vr_teleoperation/local/three_camera/launch.py \
  --mode mock --directory /tmp/openarm-three-camera-smoke --name MOCK_ONLY_TEST
"$PY" vr_teleoperation/local/three_camera/verify_mock.py \
  /tmp/openarm-three-camera-smoke/MOCK_ONLY_TEST
```

数据名重复时拒绝覆盖。mock 设备 ID、任务 prompt 和 metadata 都带明显标记，
避免把合成示范并入真机进度。原始试验缺帧现象及修复后摘要见
`mock_verification.json`；临时合成图像/构建目录检查后清理。

## 后续真实设备接入

真实路径尚未验收，先做仅相机验收，不直接批量采数据。
当前 `config.json` 的左右腕映射故意为空：

```json
{
  "wrist_left_port": null,
  "wrist_right_port": null
}
```

必须先确认 USB 2.2 / 2.3 各对应哪只物理腕部，再填成 `"2.2"` / `"2.3"` 的不同值。
序列号重复、`/dev/videoN` 顺序或屏幕左右不能作为猜测依据。
大疆当前固定检查 bus 3 端口 2.4 的 `05c6:8004`，换接线后需重新核对拓扑。

单独相机服务不含 UI/Recorder/机械臂节点；明确授权相机后才可运行：

```bash
"$PY" vr_teleoperation/local/three_camera/service.py \
  --allow-cameras --config vr_teleoperation/local/three_camera/config.json \
  --socket /tmp/openarm-camera-only.sock --max-seconds 60
```

运行时可能弹出仅针对双腕辅助程序的管理员认证。可在另一终端通过
`check_ready.py --socket /tmp/openarm-camera-only.sock` 检查三路健康；
这不等于视野、坏帧、时延或左右安装已验收。Ctrl+C 后仍应检查节点恢复。
相机服务报错不等于硬件急停。

真实采集入口需要分开确认相机与机器人；这里只展示用法，本次未执行：

```bash
"$PY" vr_teleoperation/local/three_camera/launch.py \
  --mode real --name drawer_session_001 \
  --operator 操作者 --location 现场位置
```

1. 配置未填左右映射时，在打开设备前拒绝。
2. 输入 `CAMERAS` 后才打开相机，等待三路预热/健康。
3. 输入 `ROBOT` 才调用既有网络/CAN 准备入口。
4. 既有入口仍需终端输入 `START`，此后官方 UI 会使能并启动两臂。
5. 双臂对齐和任务就位后用 X 开始，等 RECORDING；任务成功后再次 X，
   等 Saved 再复位。B 优先停臂且当前条标失败；A 不录制。
6. 默认输出到 `/media/pan/Expansion1/openarm_datasets/drawer_block/raw`，
   校验磁盘 UUID、ext4 和至少 10 GiB 剩余空间。mock 必须显式指定目录。
7. 桌面开始/成功/失败与 VR 共用同一控制器；Cancel 保留为失败，不删除原始数据。

此前左臂对齐、VR 安全键、Neutral Capture 和队列丢弃等仍有待验收项；
不能把“相机软件已接入”作为直接开始抽屉动作的授权或安全证明。

## 数据与时序

原始布局仍为 OpenArm Dataset v0.4.0：

```text
<dataset>/metadata.yaml
<dataset>/episodes/<id>/cameras/{chest,wrist_left,wrist_right}/<unix_ns>.jpeg
<dataset>/episodes/<id>/{obs,action}/arms/{left,right}/state.parquet
<dataset>/camera_timing/episode_<id>.jsonl
<output>/.capture-config/<dataset>/{metadata.yaml,camera-config.json,dataflow.yaml,...}
```

`camera_timing` 是本地侧车元数据：保留源 PTS/源接收/发布时钟、序号缺口和 mock 标记。
官方 JPEG 写入器只保存图像和主时间戳文件名，不会自动保存这些字段。
侧车不是标准转换器必然保留的部分，应随原始数据与 `.capture-config` 一起归档。

胸部使用 V4L2 源 PTS 映射到主机 Unix 时间；腕部是主机收到帧的时间，
两者均未证明等同曝光时刻。只保留新帧，不重复上一帧凑数；
中间桥接丢弃的旧样本以序号缺口显示，不把名义 30 fps 当精确帧周期。
官方动态频率推断来自 33 ms 轮询 timer（约 30.303 Hz），不是实际测得帧率；
训练重采样必须使用真实时间戳。

相机服务只缓存每路最新一帧，过期/解码异常会阻止新 episode 或触发失败。
正式 Recorder 处理 start 后回传确认，才把相机帧归到该 episode；
结束时核对已发布/应写入帧数，未齐则等待，超时将成功改为失败，不用补帧掩盖。
这是相机尾帧检查，不代表机器人总线反馈无丢包或已验证执行延迟。

缺帧/故障期间可能留下不完整 episode；不删除这些诊断数据，也不能当成成功训练集。
仍需在实机验收阶段检查 JPEG 与侧车一致性、实际帧率/抖动、长时间负载和观测/动作延迟。

## 退出与恢复

相机服务结束时关闭原生辅助程序 stdin，辅助程序检测 EOF 后停止并交还 UVC 接口。
新增二进制输出路径有离线背压/断管测试，尚未证明真实设备上所有异常退出都能恢复。
不重置带 CAN 盒的 Hub；若恢复失败明确报错，按 `../camera_bandwidth/README.md` 定向处理。
不自动写 udev/modprobe 规则、不升级固件、不安装系统包。

抽屉任务的示范定义、位置变化和建议数量见
`../../docs/抽屉放方块_数据采集方案.md`。
