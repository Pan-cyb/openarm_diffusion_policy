# 精选证据

本目录收录可随代码归档的小型原始证据。它不是新的实验，也不包含模型权重、训练数据或完整运行环境。
原始报告内部的本机绝对路径和历史字段保留原样；查看仓库副本请使用下面的相对链接。

## 原始来源与时间

| 仓库目录 / 文件 | 来源 | 性质 |
|---|---|---|
| `week1/openarm/` | 外盘 `dp_week1/outputs/openarm-week1-20261024-005817-+0800/` | 2026-10-24 基础诊断，非 learned policy |
| `week1/dp-initial/` | 外盘 `dp_week1/outputs/dp-official-20261024-005826-+0800/` | 固定种子 10000..10002，三条失败均保留 |
| `week1/dp-extra/` | 外盘 `dp_week1/outputs/dp-official-extra-20261024-011500/` | 固定种子 10003..10005，两个完成示例 |
| `week1/environment.json` | 本地 `outputs/environment.json` | 历史 11/11 环境检查，包含当时旧存储描述 |
| `week1/week1-tests.xml` | 本地 `outputs/week1-tests.xml` | 历史 80/80 测试 |
| `storage/` | 本地三个存储恢复、清理与守卫报告 | 2026-10-24 存储事件，不是模型或硬件健康验收 |
| `publication/publication-tests-20261026.xml` | 本地同名 `outputs/` 文件 | 当前 81/81 单元测试，没有新模型运行 |
| `publication/resource-guard-tests-20261026.json` | `outputs/resources/run-20261026T153332-55685.json` | 当前测试的资源守卫记录，exit 0 |
| `publication/source-pins-guard-20261026.json` | `outputs/resources/run-20261026T154049-61688.json` | 当前上游源码与精确补丁检查，exit 0 |

源文件按明确清单复制，未改写内容。初始三条失败和额外一条失败的视频均入库，
没有仅挑选成功演示。小型录像共七个，其中 OpenArm 为 30 秒、600 帧、640x480、20 fps。

## 查看入口

- [OpenArm 报告](week1/openarm/report.json)与[30 秒录像](week1/openarm/openarm-week1.mp4)
- [OpenArm 中间帧](week1/openarm/mid.png)
- [PushT 初始批报告](week1/dp-initial/report.json)与[原始指标](week1/dp-initial/eval_log.json)
- [PushT 额外批报告](week1/dp-extra/report.json)与[原始指标](week1/dp-extra/eval_log.json)
- [当前发布测试](publication/publication-tests-20261026.xml)
- [历史环境报告](week1/environment.json)
- [本地 DP 副本清理收据](storage/local-dp-backup-cleanup-20261024.json)

GitHub 若不能直接播放 MP4，可下载该小型证据文件再查看；这不需要下载 checkpoint。

## 内容校验

从仓库根目录执行：

```bash
sha256sum -c docs/evidence/SHA256SUMS
```

`SHA256SUMS` 覆盖本目录除清单自身以外的文件，路径相对于仓库根目录。
原任务书位于 `docs/reference/`，其 SHA-256 单独记录在[来源说明](../reference/README.md)。

这些哈希用于验证内容一致性，不是发布者签名或硬件健康证明。
存储恢复报告中的 `backup_retained: true` 是恢复时的状态；后来的清理收据明确记录了本地 DP 重复数据已删除。

## 不包含

- 约 4 GB 的官方 checkpoint、venv、缓存和上游 Git checkout。
- 原始完整 `outputs/`、stdout/stderr 日志、任何磁盘操作程序或 GPT/EFI 备份。
- 训练、OpenArm learned policy、π0、跨机器验收或正式 30 次评估的新证据。
