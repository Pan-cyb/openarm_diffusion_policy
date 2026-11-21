# UDP Controller-only 坑位

状态：暂停

更新日期：2026-11-07

## 已有证据

- OpenArm Pose Transmitter 补丁版默认发送到 `10.42.0.1:5006`。
- Quest 到 Ubuntu 原始 UDP 曾测得约 72 Hz。
- `dataflow_udp_pedestal.yaml` 已通过 Dora 校验。
- Quest receiver、双臂 IK 和正确 V2 pedestal MuJoCo 已同时运行。
- MuJoCo 中可看到映射后的左右目标坐标轴。
- 测试过程中观察到 `OK`、`STALE`、`INVALID` 追踪状态及恢复。

## 暂停原因

- 当前头显不适合稳定挂在脖子上。
- controller-only 仍依赖头显作为追踪基站。
- 头显位置变化会改变参考坐标并可能遮挡追踪相机。
- 当前 receiver 在完全断流时仍可能保留最后缓存，真机前缺少独立 watchdog。

## 保留文件

```text
local/dataflow_udp_pedestal.yaml
local/run_udp_pedestal.sh
downloads/OpenArm_Pose_Transmitter_0.1.0_default-10.42.0.1-patched.apk
```

## 恢复条件

满足以下任一条件后再恢复：

1. 有稳定固定支架，并验证头显相机能覆盖完整手柄工作区。
2. 找到不依赖头显佩戴的稳定 controller-only 追踪方案。
3. 用户明确要求重新研究挂脖子路线。

恢复后仍需先在 MuJoCo 验证追踪丢失、坐标跳变、漂移和断流行为，不直接接真机。
