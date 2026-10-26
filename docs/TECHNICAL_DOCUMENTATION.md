# 技术文档

更新日期：2026-10-26

## 用户 CLI 与前置条件

### 适用范围

CLI 只服务于独立 Diffusion Policy Week1：

- 官方 image PushT hybrid CNN `train_0` checkpoint 的预训练推理。
- OpenArm MuJoCo v1 基础仿真诊断。

CLI 不是训练、handover、learned OpenArm、π0/OpenPI、真机、ROS 或硬件
入口。

### 当前机器前置条件

- Linux，用户级 systemd 与 cgroup v2 可用，存在 `systemd-run`。
- 仓库本身位于本地 ext4。
- 安装脚本当前固定要求 `/usr/bin/python3.10` 和
  `/home/pan/.local/bin/uv`。
- NVIDIA CUDA 环境可满足固定的 PyTorch 1.12.1+cu116 运行检查。
- 外盘已挂载到 `/media/pan/Expansion1`，文件系统为 ext4，UUID 为
  `b8104161-e073-d644-a4b9-f4efbcfc1463`，父盘序列号为
  `00000000NT1FVEEZ`。
- 外盘工作根目录为 `/media/pan/Expansion1/dp_week1`；源码、venv 和本地
  构建 cache 不放到外盘。

这些是当前实现的固定位置，不构成任意机器可直接复用的承诺。迁移前必须核验
机器和磁盘身份，并对 `config/storage.json` 及固定工具路径做受审查的本地
适配；不得绕过守卫。

### 资源守卫

普通负载统一采用：

```bash
bash scripts/run-safe.sh \
  --budget-gib <本地新增空间GiB> \
  --external-budget-gib <外盘新增空间GiB> \
  --timeout <秒> \
  -- <命令及参数>
```

调用者必须显式声明两个磁盘预算。timeout 必须为正数且不能超过 7200 秒。
守卫还会检查：

- 本地 15 GiB、外盘 10 GiB 的保留 floor，以及命令声明的额外预算。
- 至少 2 GiB 可用 RAM。
- 8 GiB memory、512 MiB swap、最多 4 CPU。
- 单一 W1 工作负载锁。
- 外盘挂载点、ext4、UUID、父盘序列号与非符号链接路径。
- 运行中每 2 秒复查资源和外盘身份，并写入 `outputs/resources/`。

`run-safe.sh` 会为子进程设置 `DP_STORAGE_ROOT`、外盘 cache、headless
MuJoCo/SDL、禁用 W&B 和受限线程环境。

### 安装与上游恢复

安装脚本必须直接调用，因为它会自行逐步进入 `run-safe.sh`：

```bash
bash scripts/install_dp.sh
```

安装成功后如需删除项目自有 uv 构建 cache：

```bash
bash scripts/install_dp.sh --clean-local-uv-cache
```

不要执行 `run-safe.sh ... bash scripts/install_dp.sh`。安装流程依次：

1. 从 `upstream-pins.json` 创建或核验固定上游。
2. 在本地 ext4 创建 `.venv-dp`。
3. 安装固定 build tools 和 `requirements-dp.txt`。
4. 安装只带精确 metadata patch 的 Gym 0.21。
5. 以 editable 方式安装固定 DP 与 OpenArm 上游。
6. 执行 `uv pip check` 和 11 项环境检查。

Fresh clone 不包含 `upstream/` 或 `.venv-dp`。bootstrap 对已存在 checkout
不会 fetch、checkout 或 reset；提交、origin 或工作树不符合固定状态时会拒绝。
仓库会包含 `docs/evidence/` 中约 1.3 MiB 的精选小型证据和视频，但不包含大型
模型权重、外盘 cache 或原始全量输出。

### Artifact 获取

配置文件和 checkpoint 只能写入受守卫外盘：

```bash
bash scripts/run-safe.sh \
  --budget-gib 0.1 \
  --external-budget-gib 0.25 \
  --timeout 300 \
  -- .venv-dp/bin/python scripts/fetch_dp_checkpoint.py config

bash scripts/run-safe.sh \
  --budget-gib 0.1 \
  --external-budget-gib 5 \
  --timeout 7200 \
  -- .venv-dp/bin/python scripts/fetch_dp_checkpoint.py \
  --connections 4 checkpoint
```

下载器只接受固定官方 HTTPS 主机和已知字节数，支持 1 或 4 个连接、有界 Range
续传、原子发布及 receipt 校验。已有文件缺 receipt、大小或 SHA-256 不匹配时
会拒绝覆盖。receipt 中的 SHA-256 是本地计算值，不是发布者签名。

### 完整串行复验

```bash
bash scripts/verify_week1.sh
```

该脚本要求本地 `.venv-dp` 以及外盘 checkpoint 和 receipt 已存在，随后严格
串行执行：

1. pytest 与 JUnit 输出。
2. 11 项环境检查。
3. OpenArm 30 秒基础仿真诊断。
4. PushT seeds `10000..10002` 的官方预训练开发评估。

脚本为 `0644`，必须用 `bash` 调用。它会创建或覆盖仓库内的环境/JUnit 报告，
并在外盘创建带时间戳的新 OpenArm 与 PushT 目录。不要在只读审阅、文档任务
或资源未经确认时运行。

### 单项诊断

OpenArm：

```bash
bash scripts/run-safe.sh \
  --budget-gib 0.25 \
  --external-budget-gib 2 \
  --timeout 1800 \
  -- .venv-dp/bin/python scripts/openarm_week1.py \
  --duration 30 --fps 20 --width 640 --height 480 --camera auto
```

`--duration` 为 30..120 秒，`--fps` 固定为 20，图像尺寸不得低于 224x224，
camera 只能是 `auto`、`front_camera` 或 `side_camera`。`--camera-fovy`
允许 20..90 度，默认 35 度，仅作为运行时视图覆盖。

PushT：

```bash
bash scripts/run-safe.sh \
  --budget-gib 1 \
  --external-budget-gib 5 \
  --timeout 7200 \
  -- .venv-dp/bin/python scripts/eval_dp_official.py \
  --output-dir /media/pan/Expansion1/dp_week1/outputs/dp-manual-YYYYMMDD-HHMMSS \
  --n-test 3 --n-test-vis 3 --test-start-seed 10000
```

输出目录必须是 `DP_STORAGE_ROOT` 下尚不存在的新目录。`n-test` 为 1..50，
至少验证一个视频；seed 范围不能触及保留的 `100000`。默认设备是 `cuda:0`。

## 项目架构

### 固定上游

| 上游 | 固定提交 | 本地目录 |
|---|---|---|
| Diffusion Policy | `5ba07ac6661db573af695b419a7947ecb704690f` | `upstream/diffusion_policy` |
| OpenArm MuJoCo | `a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c` | `upstream/openarm_mujoco` |
| Gym | `c755d5c35a25ab118746e2ba885894ff66fb8c43` | `upstream/gym` |

Diffusion Policy 和 OpenArm 必须保持 clean。Gym 只允许
`patches/gym-0.21-metadata.patch`，不得静默升级依赖或修改策略/模型架构。

### 核心模块

| 文件 | 职责 |
|---|---|
| `config/storage.json` | 外盘身份、容量 floor、cgroup 与 timeout 配置 |
| `scripts/run-safe.sh` | 建立用户级 systemd cgroup 并转交给资源守卫 |
| `scripts/guard.py` | 锁、磁盘/RAM/cgroup/挂载检查、环境注入和资源日志 |
| `upstream-pins.json` | 上游仓库、提交、目标目录和唯一允许 patch |
| `scripts/bootstrap_sources.py` | 安全创建或验证固定上游，不重置已有修改 |
| `scripts/install_dp.sh` | 重建本地 Python 3.10 W1 环境 |
| `scripts/fetch_dp_checkpoint.py` | 固定 artifact 下载、续传、哈希、receipt 与原子发布 |
| `scripts/check_environment.py` | 版本、上游、CUDA、PushT、Gym 和 headless MuJoCo 的 11 项检查 |
| `scripts/openarm_week1.py` | OpenArm v1 关节/夹爪/相机/视频基础诊断 |
| `scripts/eval_dp_official.py` | 官方 PushT EMA checkpoint 的资源适配预训练评估 |
| `scripts/verify_week1.sh` | 最终串行编排入口 |
| `tests/` | bootstrap、存储守卫、artifact、指标/视频和 OpenArm 行为回归测试 |

### 存储分层

本地 ext4：

- 仓库源码。
- `upstream/` 固定 checkout。
- `.venv-dp/`。
- `.cache/uv` 构建 cache。
- 小型环境、JUnit 与资源日志。

外部 ext4：

- `checkpoints/` 和 receipt。
- `downloads/` 固定配置 artifact。
- `outputs/` 下的 OpenArm、PushT 视频和报告。
- 守卫注入的持久 cache。

内置盘 DP 数据重复备份已在哈希核对后按用户要求删除，不能把历史恢复报告中的
“备份保留”当成当前事实。

## 数据流

```text
fresh clone
  -> upstream-pins.json
  -> bootstrap_sources.py
  -> 本地 upstream/ 固定 checkout
  -> install_dp.sh
  -> 本地 .venv-dp

config/storage.json
  -> run-safe.sh / guard.py
  -> 外盘身份、容量、RAM、cgroup、锁与 timeout
  -> DP_STORAGE_ROOT=/media/pan/Expansion1/dp_week1

固定官方 URL
  -> fetch_dp_checkpoint.py
  -> partial + partial metadata
  -> 字节数/SHA-256/receipt 校验
  -> 外盘 checkpoint 或 config

checkpoint + 固定 DP 上游
  -> eval_dp_official.py
  -> 原始 cfg.policy + EMA state
  -> 原始 PushTImageRunner 的单环境开发评估
  -> eval_log.json + report.json + 视频

固定 OpenArm v1 上游
  -> openarm_week1.py
  -> 模型/传动/关节/夹爪/相机/视频 gates
  -> report.json + trace.npz + MP4 + 三张静帧
```

PushT 保持原生 `down_dims=[512,1024,2048]` 和 100 DDPM 步。资源适配仅包括
EMA-only 加载、`n_envs=1`、开发种子数量、线程和 CUDA memory fraction；
不修改策略架构或 runner 算法。

## 构建与验证

本项目没有独立编译产物；“构建”主要指固定上游恢复、本地 venv 安装和 artifact
准备。推荐顺序如下：

1. 阅读[项目规则](../AGENTS.md)并确认任务仍为 W1。
2. 只读核验机器、CUDA、本地 ext4 与外盘身份。
3. 按真实机器适配 `config/storage.json`，但保留等价安全门。
4. 直接运行 `bash scripts/install_dp.sh`。
5. 通过 `run-safe.sh` 获取固定 config 和 checkpoint。
6. 运行 `bash scripts/verify_week1.sh`。
7. 检查退出码、JUnit、环境报告、资源日志和外盘每个运行目录的 `report.json`；
   失败目录中的 `failure.json` 也必须保留。
8. 发布时保留 `docs/evidence/` 中的精选小型报告、配置、截图和视频；不要把
   checkpoint、venv、大型模型权重或原始全量输出提交到 Git。

### 已知验证状态

2026-10-24 的历史模型运行结果是 80/80 tests、11/11 环境检查、OpenArm
14/14 gates 与 600 帧，以及 6 条 PushT 诊断中 2 条完成、4 条失败。

2026-10-26 在资源守卫下运行完整 pytest，结果为 81/81 PASS，仓库证据为
[publication-tests-20261026.xml](evidence/publication/publication-tests-20261026.xml)。
今天没有运行新的 OpenArm 或 PushT 模型，也没有重跑完整
`bash scripts/verify_week1.sh`。因此 81/81 只能证明当前测试集通过，不能更新
历史模型指标或生成新的模型验收结论。

### 参考与精选证据

[原始四周任务书](reference/OpenArm_六名学生_四周任务书.pdf)已定位、阅读并
完成视觉检查，SHA-256 为
`c8f58f8f2a35ddbc2c9b85a53d640622a72708193432d13211fbb119f540c4ad`。
PDF 的 W1 范围为 2026-10-20 至 2026-10-26，包含官方 Diffusion Policy
示例、OpenArm 基础运行和 30 秒视频；“六名学生全部运行”尚未完成。

- [环境报告](evidence/week1/environment.json)
- [历史 Week1 测试](evidence/week1/week1-tests.xml)
- [OpenArm 精选证据目录](evidence/week1/openarm/)
- [OpenArm 报告](evidence/week1/openarm/report.json)
- [OpenArm 30 秒视频](evidence/week1/openarm/openarm-week1.mp4)
- [PushT 初始批精选证据目录](evidence/week1/dp-initial/)
- [PushT 初始批报告](evidence/week1/dp-initial/report.json)
- [PushT 初始批原始指标](evidence/week1/dp-initial/eval_log.json)
- [PushT 额外批精选证据目录](evidence/week1/dp-extra/)
- [PushT 额外批报告](evidence/week1/dp-extra/report.json)
- [PushT 额外批原始指标](evidence/week1/dp-extra/eval_log.json)
- [Week1 周报](weekly/WEEK1_2026-10-20_2026-10-26.md)

精选证据约 1.3 MiB，包含 OpenArm 30 秒 MP4、全部 6 条 PushT 小型视频、
报告、配置和截图。它们用于审阅和发布，不替代外盘中的原始全量输出。

## 限制与风险

- 本项目证明的是当前笔记本上的 W1 可运行性，不是论文性能、正式成功率或
  六名学生全部运行验收。
- PushT `max_reward_score` 不得直接改写为成功率；未达到原始完成阈值的轨迹
  必须保留为失败。
- OpenArm 是基础仿真诊断，不包含 learned task、handover 或训练。
- 外盘增加容量，不增加 RAM 或 VRAM；资源上限不能通过换存储盘解决。
- 当前安装脚本含用户路径和 Python 版本假设，移植需要显式适配与重新验证。
- Fresh clone 不含 ignored 的上游、venv、大型模型权重或原始全量输出；没有
  运行依赖和大型 artifact 时不能声称已复现历史运行。仓库内
  `docs/evidence/` 的精选小型视频和报告仍会随 clone 提供。
- 原始 PDF 已定位、阅读、视觉检查并核对 SHA-256；文档只记录已确认的 W1
  范围，不向其添加未出现的验收要求。
- GitHub 发布目标为私有 `Pan-cyb/openarm_diffusion_policy`。具体提交使用
  `git log -1 --oneline` 核对；远端配置和 `main` 状态使用 `git remote -v`
  与 `git ls-remote origin refs/heads/main` 核对，本文不固化可变状态。
- W2、训练、数据采集、π0/OpenPI、learned OpenArm、handover 和硬件工作都
  需要用户新的明确人工授权。

相关入口：[README](../README.md)、[项目状态](PROJECT_STATUS.md)、
[交接文档](HANDOFF.md)和[Week1 报告](WEEK1_REPORT.md)。
