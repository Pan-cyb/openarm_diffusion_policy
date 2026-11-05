# 项目状态

更新日期：2026-11-05

阶段：OpenArm V2 主线初始化

## 当前结论

- 硬件形态确认为普通 OpenArm 2.0 双臂加底座，不是 OpenArm Cell。
- V1 Week1 项目已从当前本地工作区移除，历史仍可从 Git 远端恢复。
- 官方 V2 `pedestal` 模型已提取为独立快照并保留 Apache-2.0 许可证。
- 父目录 `.venv-dp` 已成功加载和启动 V2 `pedestal` 模型，可以继续复用。
- `.venv-dp` 的 OpenArm MuJoCo 与 Diffusion Policy 源码路径已重新指向
  `openarm_v2/upstream/`。
- Diffusion Policy 和 Gym 固定源码已移动到本项目 `upstream/`。
- 当前没有 CAN、ROS、真机读取或真机运动。

## 已验证基线

```text
scene: vendor/openarm_mujoco/v2/pedestal/pedestal.xml
nq: 18
nv: 18
nu: 16
nbody: 21
njnt: 18
ncam: 2
nkey: 1 (home)
```

## 下一步

1. 输出 18 个关节与 16 个执行器的名称、地址、单位和范围。
2. 在仿真中完成单关节、小幅度、可复现控制练习。
3. 整理两台腕部相机的观测接口。
4. 再定义未来 Diffusion Policy 的 observation/action 契约。
