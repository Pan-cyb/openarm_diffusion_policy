# 四路采集主线交接

## 后续补充（2026-11-21）

新增 `basic_image_ready` 近黑门槛，真实入口在 `ROBOT` 确认前后重新检查，
真实桥接开始/记录中也强制检查，不能仅靠人工标志放行。
新增 9 项离线回归，four_camera 当前 37 项，合计 140 项 Python。
保存的 121 张真实暗场图离线回放被正确拒绝；新版 mock 四路各 31 JPEG、
四组关节各 30 行完整读回。未新开设备，正常场景开销尚未实测。
仍等待用户确认现场照明/遮挡，目标不标完成。

## Current Goal

用户明确要求持续目标：把当前四路真实采集整理到可用为止。目标仍 active，
不是完成；正常照明/画质/正常带宽长测待确认，不自动转去训练或机器人运动。

## Important Context

路径 `/home/pan/openarm_diffusion_policy`。父仓库有用户原改动且整个
`vr_teleoperation/` 未跟踪，未提交。请先读根与子项目 AGENTS、PROJECT_STATUS 顶部、
`四路相机采集验收.md` 和 `local/four_camera/README.md`。
只获相机/离线/存储授权，禁止 CAN/串口、运动、主机自动重启、集线器重置、
BIOS/驱动/接线供电修改。不要保存或重复用户口令。

## Completed This Session

- 四路微雪正式软件链路：固定身份/方向、服务、独立 raw drain、因果 snapshot、
  全局相机 Dora/Recorder/UI 门控、保留旧三路兼容。
- 原生 helper 临时 profile 读回/恢复；暗场 AE priority1 约 10 fps，
  priority0 约 30 fps。手动 10 ms 也黑，不是已验曝光方案。
- 真实相机＋合成关节在 Expansion 落盘并读回，最新 121 JPEG 和四组各 30 行关节。
- 发现服务 SIGKILL 后 sudo 管道退出造成接口未恢复，失败保留；
  增加用户态 native_guard 后，服务强杀、接口恢复、四路重开、干净停止通过。
- 恢复失效试验留下的 AE priority 改动。最后原生日志确认全部
  before1/applied0/restored1，八个 UVC 接口恢复，取流停止，sudo 授权撤销。

## Changed Files

主改动：`local/four_camera/`，`local/camera_bandwidth/{commission_profile.py,
restore_ae_priority.py,query_v4l_controls.c,uvc_pair_probe.c}`，
以及 `local/three_camera/` 的显式相机数/开始门控/新控制诊断兼容。
详细入口、验收和证据索引见上方文档，不改固定上游或官方模型。

## Verification

73 three_camera + 30 camera_bandwidth + 28 four_camera = 131 Python 测试通过；
6 组原生离线测试通过；真实派生图 Dora validate 通过，没有启动真实机器人图。
真实诊断：
`/media/pan/Expansion1/openarm_datasets/diagnostics/20261120_four_pipeline_guard_dark`。
其 `.capture-config` 同名目录含完整原生控制/温度/接口收尾日志。
本地汇总：`local/camera_bandwidth/evidence/2026-11-20-commissioning/README.md`。

## Known Issues

- 四路真实图像均近黑，已问现场是否关灯/遮挡，尚无回复；
  不继续在黑图下做重复或长测，不声称正常画面吞吐与曝光已验。
- 原主机卡死根因、整机断电恢复、光学曝光时延、真实关节同步、Quest 实际显示未验。
- 相同 fps 不等于曝光同步，当前时钟为主机接收。
- 真实关节门控非完整序列验收，合成 30 行只是格式/软件边界验证。

## Next Recommended Steps

确认正常采集照明和镜头无障碍后，先审四路实际画面及关键任务区域，再短测，
通过才逐级 30/120 秒；有新硬件错误、坏帧、间隔/序号异常或恢复失败即停。
不要删除原旧 0 字节 JPEG、本轮失败目录或外盘诊断数据。独立 worker 临时源码
已合并审阅后清理，未新增下载/安装，active 构建保留。

## Resume Instructions

先只读检查进程/接口/Expansion，确认用户光照回复；不要直接运行旧 DJI 三路入口。
当前 primary 仅询问照明，并没有获准打开机械臂或自动重启主机。
