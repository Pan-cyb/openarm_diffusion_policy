# 工作区规则

## 目录边界

- `openarm_v2/` 是 OpenArm 2.0 主线，只使用 V2 模型和资料。
- `vr_teleoperation/` 是独立的 Quest WebXR 仿真遥操作项目，只能使用
  `openarm_v2/` 固定的 V2 pedestal 模型。
- `two_joint_teaching_arm_demo/` 是独立教学实例，不作为 OpenArm V2 参数、
  控制或真机证据。
- 不得重新把 OpenArm V1 代码、模型或验收结论混入 V2 主线。

## 环境

- 根目录 `.venv-dp/` 是暂时共享环境，当前能够运行 MuJoCo 3.6 和 V2 模型。
- `openarm_v2/upstream/` 保存固定 Diffusion Policy 和 Gym 源码，不提交到
  当前仓库。
- `openarm_v2/vendor/openarm_mujoco/v2/` 是官方 V2 模型快照，必须保留来源
  和许可证。

## 安全边界

- 默认只允许离线文件检查和 MuJoCo 仿真。
- 不得从仿真结果推断真机接口、零位、方向、标定或安全状态已经验证。
- 未经新的明确授权，不得打开 CAN/串口、使能电机、发送运动指令、修改电机
  ID、零位、固件、接线或供电。
- 真机工作必须从只读身份和状态检查开始，并与仿真证据分开记录。

## 工作方式

- 文档使用简洁的简体中文。
- 保持三个项目相互独立，不共享实验结论。
- 不删除 `.venv-dp/` 或外盘数据，除非用户再次明确要求。
- 不修改 `vendor/` 中的官方模型；实验改动应放在项目自有脚本或单独派生模型中。
