# 2026-11-20 四路微雪与重启恢复交接

## Current Goal

只做已授权的四路相机测试，确认物理身份、方向、并发和诊断落盘；
用户报告卡死后转为只读恢复与离线文件核验，不再增加取流负载。

## Important Context

- 本轮从未打开 CAN/串口或发送机器人命令；没有正式任务示范/训练。
- 用户最新确认：bus 3 的 2.2 右腕、2.3 左腕、2.4 胸部、1 全局；
  左右腕和全局旋转 180°，胸部不旋转。
- 根工作树原有修改及未跟踪内容均保留，不提交或回滚。
- 当前配置/证据入口：
  `../../local/camera_bandwidth/four_camera_layout.json`、
  `../../local/camera_bandwidth/evidence/2026-11-20-four-camera/README.md`。

## Completed This Session

- 读取用户指定历史聊天「规划摄像头数据采集与训练」，核对旧双腕 180°
  和进程内 payload=1600 方案；检查当前四路设备和重复序列号。
- 新增四端口白名单支持及相机专用诊断脚本，旧默认双腕行为不变。
- 逐路样图、旧双腕基线、四路 1600 失败与四路 1280 约 60 秒对照均记录。
- 约 5 秒原始 JPEG 落盘后发生主机卡死，用户重启；系统启动时间 22:30:37。
- 只读保存前一启动的内核记录；22:27:52 有 CPU/L1 已纠正错误，原因未定。
- 节流离线读回：511 个路径/原生计数一致，510 张正常，胸部最后一张 0 字节。
  失败原件保留，没有删除、修复或重采覆盖。
- 按新映射生成标准方向预览，更新项目规则、状态、操作入口警告。

## Changed Files

- `local/camera_bandwidth/uvc_pair_probe.c`、`test_native_cli.py`。
- 新增 `check_four_cameras.py`、`run_four_camera_probe.py`、
  `verify_four_camera_disk.py`、`four_camera_layout.json`。
- `local/three_camera/image_orientation.py` 和对应方向测试。
- `local/camera_bandwidth/evidence/2026-11-20-four-camera/`。
- 项目 AGENTS、README/状态/交接/流程入口；未改固定 vendor/upstream 或机器人图。

## Verification

- 重启前：严格 C 编译、14 项相机 Python 回归、6 项原生离线测试通过。
- 物理短测有真实负面结果：1600 第四路带宽不足；1280 帧率不齐、存在缺口。
- 重启后：4 项方向单测通过；完整逐帧读回以非零状态结束，正确报告一张 0 字节。
- 预览只用保存帧，重启后没有重开相机；没有修复磁盘或运行硬件压力测试。
- 原始数据约 100 MiB 保留在 Expansion `openarm_datasets/diagnostics/2026-11-20_four_micro_p1280`；
  临时 worker 目录随重启清除，保留已构建工具及依赖、日志和失败证据。

## Known Issues

- 不能把“收流/解码正常、文件存在”当作重启后持久化通过。
- 不能把已纠正 MCE 直接归因为卡死原因，也不能宣称与本次负载无关。
- 旧 `three_camera/` 仍依赖 DJI 胸部，并非现在的四路微雪生产入口。
- host receipt timestamp 不是曝光同步，尚无机器人动作/反馈对齐的本轮证据。
- 没有主机稳定性、长期并发、关键抓取视野或正式采集验收。

## Next Recommended Steps

1. 先只读排查主机异常，必要的硬件测试或系统修改另行明确授权。
2. 为正式保存实现结束持久化确认及重启后完整性检查，保留当前失败用例。
3. 主机稳定后将四路微雪和已确认角色/方向接入正式采集；不要启动旧 DJI 图替代。
4. 再在明确现场授权下做短测和逐条质检，未通过前不批采、不训练。

## Resume Instructions

先读项目 `AGENTS.md`、`../PROJECT_STATUS.md` 和证据 README。
不要因历史存在 CAMERAS/START 命令自动启动设备；不要清理外盘原图或失败文件。
