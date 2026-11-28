# 抽屉任务：三条示范的离线试训

目标是验证真实数据处理、GPU 训练和独立轨迹上的动作预测，不是部署或测量任务成功率。没有机器人接口。只读 2026-11-22 合并集，固定使用来自三个批次的编号 `0、26、33`；前两条训练，最后一条验证。不要把此验证条再当作未来的盲测集。

从仓库根目录执行（数据和训练输出均须使用新目录）：

```bash
S=vr_teleoperation/local/dp_pilot
O=vr_teleoperation/outputs/dp_pilot_20261123
D=/media/pan/Expansion1/openarm_datasets/drawer_block/raw/real_20261122_camera_adjusted_new_block
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python -B "$S/prepare.py" "$D" "$O/data"
export PYTHONPATH="$PWD/openarm_v2/upstream/diffusion_policy"
.venv-dp/bin/python -B "$S/train.py" "$O/data" "$O/run" --steps 600
.venv-dp/bin/python -B "$S/train.py" "$O/data" "$O/run3000" --steps 3000
.venv-dp/bin/python -B "$S/plot_gripper.py" "$O/data" "$O/run3000"
```

- 预处理在四路相机和四路关节流共同覆盖区间建立 10 Hz 网格，只取不晚于锚点的最近样本；任一路样本年龄达到 100 ms 则拒绝该条，不跨缺口拼接。时间戳是主机接收时间，不是曝光标定。四路 JPEG 不再旋转，按原比例缩到 96×54，保存 RGB。`preview*.jpg` 是缩略抽帧，不代替逐条任务成功验收。
- 输入是过去和当前两个时刻的四视角图像和 16 维关节反馈；目标是从下一时刻起 8 步（0.1 至 0.8 秒）的 16 维 leader/IK 关节目标。窗口不跨轨迹，归一化只用训练轨迹。每一维按训练极差的一半缩放，半极差下限 0.01。
- 轻量共享视觉 CNN 配合固定上游的 `ConditionalUnet1D` 和 32 步 DDPM；不是官方 PushT 成绩或原版全尺寸视觉配置。两次独立初始化使用相同随机种子，不依据验证 loss 选最佳 checkpoint。
- `metrics.json` 对每条轨迹取均匀分布的 48 个窗口，以固定采样噪声比较训练前后。`normalized_mae` 为所有未来步和 16 个通道的归一化绝对误差均值，越低越好；不是弧度或成功率。`hold_feedback_normalized_mae` 使用当前实测位置预测未来全部目标。切换窗口以窗口内右夹爪目标极差超过该通道训练缩放因子的 20% 定义，不是人工阶段标签。
- `right_grip.png` 单独检查验证轨迹：每隔 0.2 秒预测一次 0.4 秒后的右夹爪位置目标，横轴为目标时刻。`pilot.pt` 是离线试验权重，不是可执行策略。

## 49 条数据的第一版基线

沿用上面的 `S`、`D` 和 `PYTHONPATH`，输出另存，不覆盖试训结果：

```bash
O=vr_teleoperation/outputs/dp_baseline49_20261123
P=vr_teleoperation/outputs/dp_pilot_20261123/run3000/pilot.pt
vr_teleoperation/upstream/dora-openarm-data-collection/.venv/bin/python -B "$S/prepare.py" "$D" "$O/data" --all49
.venv-dp/bin/python -B "$S/train.py" "$O/data" "$O/run" --steps 10000 --reference "$P"
.venv-dp/bin/python -B "$S/plot_gripper.py" "$O/data" "$O/run" --episode 7
```

按三批示范的来源预留 `7、19、29、37、45`，不包含三条试训用过的轨迹。自动质检拒绝项记入清单，不补帧，不从训练集中临时挑替代验证条。评估采用同一验证窗口、逐条固定噪声和本轮训练集的尺度；旧模型使用自己保存的输入输出归一化，预测先还原单位再计分。`reference_validation` 为旧模型，`matched_steps` 为新模型与旧模型同训练步数时的评估，`after` 为预定末步。升降窗口以未来目标的首末变化超过训练极差的 10% 区分，不等同于实物抓取或释放成功。
