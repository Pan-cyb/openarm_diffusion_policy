# 真实数据检查与 DP 对接

更新：2026-11-21。仅离线读文件、审片和用户授权的数据归档；
没有访问 CAN/串口、发送运动命令、修改驱动或启动训练。

## 更新结论与训练排除

用户随后确认相关线缆有一处虚焊，处理后 J2 反馈恢复；具体线位未说明。
新会话 `real_20261121_170001` 第 0 条中，J2 位置/速度/力矩均更新，
四路各 314 JPEG 全量检查通过，报告在同一 reviews 根目录下的新会话文件夹。

但用户明确指定：**截至该会话第 0 条（含）全部为无效采集，不进入训练**，
包括这条成功的修复验证，以及下文旧会话中的 0～3 条。
统一依据为 `/media/pan/Expansion1/openarm_datasets/drawer_block/training_exclusions.json`。
不得仅因 success、完整性通过或 J2 后来恢复而重新纳入旧数据。
原始文件和旧报告保留作排障证据，不改保存结果，不动活动 Recorder 的 metadata。
当前会话第 1 条及以后或更新会话按正常任务质量另行判断，不在此次排除范围。

## 本次文件与结论

会话：`/media/pan/Expansion1/openarm_datasets/drawer_block/raw/real_20261121_153802`。
检查结果与两张四路抽帧图：
`/media/pan/Expansion1/openarm_datasets/drawer_block/reviews/real_20261121_153802/`。

| 原始编号 | 录制边界时长 | 四路 JPEG 数量 | 双臂反馈/动作 | 结论 |
| --- | --- | --- | --- | --- |
| 2 | 4.90 秒 | 各 141，合计 564 | 反馈各 1225 行、动作各 1224 行 | 文件完整；动作很少、夹爪未变化，不能据此认定完整任务成功 |
| 3 | 28.94 秒 | 全局/胸部各 858、双腕各 857，合计 3430 | 四组各 7235 行 | 文件完整，有动作和夹爪变化；任务成功仍需审完整片段 |

3994 张 JPEG 全部解码、源哈希、字节数、源序号、发布/保存集合和起止边界通过；
四路约 29.91 fps。Parquet 各臂 qpos 为 8 维，反馈另有 qvel/qtorque，全部数值有限，
时间戳严格递增，反馈/动作实测约 250 Hz。异步流相差一行并不代表自动丢样。

以共同覆盖区间的 20 Hz 时间网格试算：
第 2 条最大图像帧龄 34.04 ms、四路跨度 32.40 ms、关节样本帧龄 4.73 ms；
第 3 条分别为 36.01 ms、35.65 ms、4.79 ms。
50 ms 图像与 20 ms 关节界限是本次检查参数，不是 DP 的硬件同步标准。

**原始文件及主机时间匹配可用于后续 DP 对接，但尚未验证 DP loader 或训练。**
录制的 `success` 表示人工请求成功且保存检查通过，不自动证明物体已成功放入抽屉。
抽帧中抽屉起始已打开，不能直接按元数据中“初始抽屉关闭”的任务当成标准成功示范。
腕部图像有部分遮挡/强反光，完整抓取与释放的可见性需要人工审片。

## 此前 J2 问题与后续恢复

两条的左臂 J2（qpos 下标 1）反馈 qpos、qvel、qtorque 全为 0。
第 3 条该轴目标从约 -0.309 到 +0.153 rad 有变化，并非固定零目标。
当前 pedestal 配置没有将该轴锁定，目标也在该轴配置限位内。

底层电机状态初始值为零；缺少有效回包时存在未更新/缓存语义。
Recorder 目前只保留 qpos/qvel/qtorque/pose，不保存 motor_status/bus，
所以仅凭这些历史文件不能区分真实静止、未更新反馈和其他原因。
不要自动把零替换成目标、插值补造反馈，或凭此更改电机参数。
后续修复验证已看到反馈恢复；旧数据仍按用户决定排除，不能补造反馈后投入训练。

当前 action 是 leader/IK 目标，不是反馈，也不是已经证明完整记录的驱动裁剪后目标；
训练与部署必须保持相同动作含义和安全处理链路，不能用 obs 代替 action。

## 官方 DP 的帮助与不同点

参考的是异步采样、带时间戳的历史缓存和因果选帧设计，
不是直接把 RealSense 驱动拿来驱动微雪 USB 相机。
官方 `RealEnv.get_obs()` 为目标观测时间挑选此前最近相机帧和机器人状态，
相机率、关节率和策略率可以不同；`SingleRealsense` 使用独立采集进程和环缓存。
这不是共同硬件触发，也不能消除所有相机曝光延迟。

本项目使用自己的微雪取流、环缓存、独立 raw drain，原始 JPEG/关节流异步落盘；
snapshot 取不晚于锚点的最近图像。**当前原始数据还不是固定 20 Hz 训练数组。**
转换时应截取四路图像和四组关节都有覆盖的时间段，建立统一网格，
每路取不晚于目标时刻的最近有效样本，并保留帧龄/缺口检查。
真实主机接收时钟不是曝光时刻，也不是电机完成动作的时刻。

官方 PushT 配置是两维末端任务，不能原样套在双臂上。
本项目可采用四路 RGB、双臂共 16 维反馈、16 维关节目标动作；
还需实现本 V2 数据集的读取/转换、图像尺寸与 RGB/CHW、序列采样、
训练/验证按 episode 划分及训练集归一化。本轮不扩展训练框架、不启动训练。

## 初始状态建议

DP 不强制每条所有关节一模一样，官方固定关节初始化是可选项。
但最初一批正式示范建议保持相近、可复现的双臂起始姿态、夹爪状态、
抽屉开合、物体摆放和相机安装；部署也从相同分布开始。
基准流程稳定后，再有计划地增加小范围初始变化，而不是随意混入不同任务阶段。
启动、对齐和人工复位在 X 录制外完成，接近、抓取、释放和成功末态录完整。

## 前两条的归档

用户明确指定原始 0、1 两条为废数据，已移至：
`/media/pan/Expansion1/openarm_datasets/drawer_block/unused/real_20261121_153802`。
对应图像/关节目录、时钟侧车一并移动，原会话 metadata 只保留 2、3；
归档 metadata 保留 0、1，原完整 metadata 另有备份，`quarantine.json` 记录原因。
不改原编号、不删图、不删失败证据；4770 个文件同盘重命名后设备号/inode/
大小/修改时间全部一致，共 985427967 字节。
操作前确认 Recorder 已退出，避免其缓存索引随后覆盖手工整理结果。

复查命令（只读原始数据，报告目录须用新名称）：

```bash
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python \
  vr_teleoperation/local/four_camera/audit_real_dataset.py \
  /media/pan/Expansion1/openarm_datasets/drawer_block/raw/real_20261121_153802 \
  --episodes 2 3 --output /media/pan/Expansion1/openarm_datasets/drawer_block/reviews/NEW_REVIEW
```

## 官方来源

- [RealEnv：异步观测对齐、动作时间与 episode](https://github.com/real-stanford/diffusion_policy/blob/main/diffusion_policy/real_world/real_env.py)
- [SingleRealsense：独立进程和环缓存](https://github.com/real-stanford/diffusion_policy/blob/main/diffusion_policy/real_world/single_realsense.py)
- [RealPushTImageDataset：序列、图像维度、动作读取](https://github.com/real-stanford/diffusion_policy/blob/main/diffusion_policy/dataset/real_pusht_image_dataset.py)
- [官方 PushT shape_meta](https://github.com/real-stanford/diffusion_policy/blob/main/diffusion_policy/config/task/real_pusht_image.yaml)
- [评估脚本：初始场景叠图匹配、可选关节初始化](https://github.com/real-stanford/diffusion_policy/blob/main/eval_real_robot.py)
