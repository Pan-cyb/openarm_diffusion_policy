# 项目代理规则

## 范围

- 本仓库是独立的 Diffusion Policy Week1 项目。
- 仅覆盖官方图像 PushT 预训练推理，以及 OpenArm MuJoCo v1 模型加载、关节控制、相机和夹爪观测。
- π0/OpenPI、handover、W2/W3 数据与训练、真机、ROS、硬件和系统分区均不属于本项目。
- 不得安装 π0/OpenPI 源码或训练数据，不得自动启动下一阶段。

## 固定上游

- Diffusion Policy：`5ba07ac6661db573af695b419a7947ecb704690f`
- OpenArm MuJoCo：`a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c`
- Gym：`c755d5c35a25ab118746e2ba885894ff66fb8c43`
- Gym 只允许 `patches/gym-0.21-metadata.patch`；不得静默升级或修改 DP/OpenArm 模型架构。

## 固定方案

- 使用官方 image PushT hybrid CNN `train_0` checkpoint。
- 保持原生 `down_dims=[512,1024,2048]` 和 100 DDPM 步。
- W1 使用预训练推理，不是从头训练或论文基准复现。
- OpenArm 只做基础仿真诊断，不建立 learned task 或 handover。

## 安全与存储

- 普通负载必须通过 `bash scripts/run-safe.sh`，并显式给出本机预算、外盘预算和 timeout。
- `bash scripts/install_dp.sh` 直接调用，由其内部逐步使用守卫；不得双重包裹。
- 最终串行入口为 `bash scripts/verify_week1.sh`，文件保持 `0644`，不得依赖可执行位。
- 外盘身份门必须匹配 UUID `b8104161-e073-d644-a4b9-f4efbcfc1463`、父盘序列号 `00000000NT1FVEEZ`、ext4 和 `/media/pan/Expansion1`；`/dev/sda3` 只是可变观察名。
- 不得把未挂载的同名普通目录、预留系统区或共享区当作 DP 工作区；挂载路径变化须重新核对并更新配置。
- 源码、venv 和构建 cache 继续留在本地 ext4；大 checkpoint、视频和持久证据放在 `/media/pan/Expansion1/dp_week1`。
- 不得格式化、分区或修改系统盘。

## 证据边界

- “本机 W1 可运行性验收”不等于策略性能成功。
- PushT `max_reward_score` 是原始 runner 指标，不是二元成功率。
- 未达到原始任务完成阈值的轨迹必须保留为失败，不得筛除或改写。
- 额外固定种子批次只能作为同权重、同参数、无调参的 W1 诊断，不得用于种子筛选、训练或正式 benchmark。
- OpenArm runtime FOV 只允许改善视图可读性，不得改变 pose、dynamics、scene 或 source model。
- 只验证当前笔记本，不得宣称六名学生或六台机器完成。
- W1 收口后的任何新目标都需要用户明确批准。

## 工作方式

- `gpt-6-astra` 负责范围、方案、验收标准和最终审查。
- 边界清晰的实现或测试使用 `gpt-5.6-sol` worker；失败时如实记录，不得静默换模。
- worker 不得扩大写入范围或创建嵌套代理；主代理必须复核证据。
- 文档使用简洁的简体中文。

## Git 归档

- 提交代码、文档、原始任务书和经核对的小型精选证据；不提交 venv、缓存、上游 checkout、模型权重、原始 outputs、凭据或磁盘操作备份。
- 周记逐项对照任务书，区分历史模型结果与当前测试；不得把单机 W1 完成写成六人验收或完整论文复现。
- 仓库文档使用相对链接；本机磁盘路径只作为配置说明，不作为 GitHub 上的证据链接。
- 发布前检查暂存内容和敏感信息；不得擅自公开私有仓库或修改上游许可证。
