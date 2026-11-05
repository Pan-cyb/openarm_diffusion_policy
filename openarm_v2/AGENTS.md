# OpenArm V2 主线规则

## 范围

- 当前对象是普通 OpenArm 2.0 双臂加底座。
- 仿真入口固定为 `vendor/openarm_mujoco/v2/pedestal/pedestal.xml`。
- 当前阶段只覆盖模型加载、结构检查、关节控制和相机观测。
- Diffusion Policy 源码保留用于后续学习，但当前不采集数据、不训练策略。
- 不包含 OpenArm Cell、V1、ROS、CAN 或真机运动。

## 固定来源

- OpenArm MuJoCo V2：官方提交
  `a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c` 的 V2 快照。
- Diffusion Policy：
  `5ba07ac6661db573af695b419a7947ecb704690f`。
- Gym：`c755d5c35a25ab118746e2ba885894ff66fb8c43`，只保留
  `patches/gym-0.21-metadata.patch`。

## 约束

- 不修改 `vendor/openarm_mujoco/v2/` 中的官方文件。
- 所有实验代码写在 `scripts/`、后续 `src/` 或明确命名的派生模型目录。
- V2 仿真结论不能作为真机标定或控制验证。
- 未经明确授权，不添加或运行硬件通信代码。
