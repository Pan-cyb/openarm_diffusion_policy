# 官方来源

## OpenArm 总入口

- 仓库：`https://github.com/enactic/openarm`
- 用途：硬件、装配、电子、软件和相关官方仓库索引。

## OpenArm MuJoCo V2

- 仓库：`https://github.com/enactic/openarm_mujoco`
- 固定提交：`a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c`
- 包版本：`2.3.0`
- 本项目保留内容：该提交中普通双臂、底座和对应资产，以及 Apache-2.0
  许可证。
- 仿真入口：`v2/pedestal/pedestal.xml`。

当前快照只保留普通双臂加底座所需的 V2 内容；V1、V0.3、Cell、网页演示和
上游开发工具没有复制到可提交快照。完整 V2 稀疏 checkout 位于本地
`upstream/openarm_mujoco/`。

## Diffusion Policy

- 仓库：`https://github.com/real-stanford/diffusion_policy`
- 固定提交：`5ba07ac6661db573af695b419a7947ecb704690f`
- 当前状态：仅保留源码和已有环境，尚未建立 OpenArm V2 learned task。

## Gym

- 仓库：`https://github.com/openai/gym`
- 固定提交：`c755d5c35a25ab118746e2ba885894ff66fb8c43`
- 唯一修改：修正 `opencv-python>=3.` 的包元数据为
  `opencv-python>=3.0`。
