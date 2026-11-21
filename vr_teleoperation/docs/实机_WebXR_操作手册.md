# OpenArm V2 Quest WebXR 实机操作手册

更新日期：2026-11-16。真实采集请同时读 `数据采集操作流程.md`；
下面基础遥操入口不是受保护的三相机采集入口。

## 1. 安全原则

- 操作时必须有人留在硬件空开旁。
- 开始前清空双臂完整运动范围，不得有人站在机械臂前方或夹爪之间。
- VR 的 `B`、电脑 UI 的 `Arm Stop` 和终端 `Ctrl+C` 都是软件停止，
  不能替代硬件空开。
- 机械臂没有抱闸，disable 或断电后可能因重力自然下落。
- 不运行 `openarm-can-cli set_zero`，除非明确要把当前位置写成新的电机零位。
- 不把 `openarm-can-zero-position-calibration` 当作普通回初始姿态命令。

发生碰撞趋势、错误方向、剧烈抖动、异常声响或软件停止无响应时，立即使用
硬件空开。

## 2. 设备与接口

```text
Quest 3
  -> USB ADB reverse: localhost:8080
  -> pxy111 局域网 WebRTC
  -> WebXR
  -> V2 pedestal IK
  -> dora-openarm
  -> can0 右臂 / can1 左臂
```

CAN 盒：

```text
物理 CAN1 -> Linux can0 -> 右臂
物理 CAN2 -> Linux can1 -> 左臂
```

## 3. 电脑端启动

机械臂和 CAN 盒接线确认后，在 Ubuntu 执行：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_webxr_real_pedestal.sh
```

2026-11-21 起，同一命令默认进入四路微雪采集，不再进入旧的纯遥操流程。
启动后自动检查四路和数据盘，四路就绪后只输入一次 `START`，
同时确认下面的网络/CAN 准备和机械臂启动；不再问 `CAMERAS`、`ROBOT`、操作者或地点。
操作者默认当前用户名，地点记 `unspecified`，有需要时用参数覆盖。
旧会话不会因改脚本自动切换，先按已有安全停机流程退出，再运行上面的同一命令。
完整录制步骤见 `数据采集操作流程.md`：对齐后 X 开始、再 X 成功结束，B 停臂/失败。

网络传输固化服务会在开机、上游 Wi-Fi 重连和 Quest USB 重连后自动维护
`pxy111` 与 ADB reverse。相机和准备脚本可能分别提示管理员授权；
`START` 确认后会再次检查或恢复以下状态：

1. 创建或恢复 `pxy111` 并发热点。
2. 让热点信道跟随上游 `wlp4s0` 的当前信道。
3. 配置 `can0`、`can1` 为 `1 Mbps / 5 Mbps CAN FD`。
4. 确认 Quest `localhost:8080` 的 ADB reverse。
5. 显示运动警告并等待终端确认。

确认现场安全后，在相机启动器提示时输入一次（后续不再重复询问）：

```text
START
```

`START` 是运动确认词，不是密码。输入后，官方 UI 会使能双臂并移动到 pedestal
初始姿态。

电脑状态 UI：

```text
http://127.0.0.1:8000/
```

## 4. Quest 进入 WebXR

脚本启动后，在 Quest Browser 打开：

```text
http://localhost:8080/
```

点击页面 `Start`，进入透视 WebXR。

如果页面不正常：

1. 确认 Quest USB 调试仍已授权。
2. 确认 Quest 已连接 `pxy111`。
3. 确认电脑端 `ap0` 地址为 `10.42.0.1`。
4. 退出旧 WebXR 会话并重新打开页面。
5. 裸 8080 会自动进入内容版本路径；核对页面 UI 哈希，不再使用日期后缀。
   软件修改后需要重启服务；本次未进行 Quest 内更新验收。

## 5. 首次对齐

双臂刚启动时，电脑 UI 显示：

```text
L: STARTED
R: STARTED
```

此时机械臂在官方初始姿态，但尚未和当前手柄 IK 目标接续。

对齐步骤：

1. 双手控制器放在胸前舒适位置并保持不动。
2. 左手 Trigger 保持松开。
3. 右手 Trigger 按到约 75% 以上并保持。
4. 等待右臂缓慢移动，直到 UI 显示 `R: ALIGNED`。
5. 松开右手 Trigger。
6. 保持右手不动，以相同方式对齐左臂。
7. 等待 UI 显示 `L: ALIGNED`。

对齐是从机械臂当前关节位置缓慢过渡到 VR IK 目标，避免连接瞬间跳变。

## 6. 夹爪控制

Trigger 在对齐后控制夹爪：

```text
Trigger 松开 -> 夹爪打开
Trigger 按下 -> 夹爪闭合
```

右、左夹爪方向在电机坐标中相反，但 WebXR 已完成映射，操作者统一按
“松开为打开、按下为闭合”使用。

侧面 Grip 当前不使用。

## 7. VR 安全控制

头显内 HUD 显示以下控制：

| Quest 按键 | 功能 |
| --- | --- |
| 右手 `B` | Arm Stop，并立即停止浏览器发送手部 pose |
| 右手 `A` 长按 1.5 秒 | Arm Start |
| 左手 `X` | 仅三相机采集入口：READY 时开始，RECORDING 时请求成功结束 |
| 左手 `Y` 长按 2 秒 | 在官方初始姿态执行 Home Neutral Capture |
| Trigger | 夹爪；在 `STARTED` 阶段许可初始对齐 |
| Grip | 当前未使用 |

状态：

- HUD、HTTP 控制端点和 Dora 命令合并节点已完成离线验证。
- 截至 2026-11-16，本轮 X 采集和改后的 HUD 仅离线验证，未完成实机验收。
- 三相机入口 B 会额外请求将当前 episode 保存为失败；正常成功结束用 X，
  等待 Recorder 确认后再复位。普通遥操入口 X 录制禁用。
- 在实机验收完成前，电脑 UI 和硬件空开必须保持可用。

## 8. 常用操作

### 8.1 暂停遥操

1. 控制器保持静止。
2. 按右手 `B`，或在电脑 UI 点击 `Arm Stop`。
3. 等待 UI 显示左右臂 `STOPPED`。

软件 Stop 会执行官方 stop 轨迹并 disable。禁用后机械臂可能自然下落。

### 8.2 回到官方初始姿态

1. 先按 `B`，等待左右臂 `STOPPED`。
2. 双手放回舒适中立位置。
3. 长按右手 `A` 1.5 秒。
4. 双臂移动到官方 pedestal 初始姿态，状态变为 `STARTED`。
5. 分别按住右、左 Trigger 重新对齐。

### 8.3 Neutral Capture

Neutral Capture 不要求人手和机械臂在真实空间重合。它把舒适手位直接映射到
固定 V2 pedestal 模型 `home` keyframe 的左右末端位置：

1. Arm Start 后保持双臂在官方初始姿态，UI 为 `STARTED`。
2. 暂时不要 Trigger 对齐。
3. 把双手放到舒适的中立位置。
4. 长按左手 `Y` 2 秒。进度条完成后，浏览器冻结 pose 输出。
5. 系统从模型 home FK 为左右手分别计算 offset 候选并取平均。
6. 结果保存到 `local/neutral_capture.yaml`，当前进程立即加载。
7. 长按 `A` 重新执行官方初始姿态，再分别使用 Trigger 对齐。

如果左右候选相差超过 `0.20 m`，系统拒绝保存并保持暂停。此时按 `B` 停止，
重新启动后再试。

### 8.5 真正急停

发生危险时直接切断硬件空开。

不要等待：

- VR `B`；
- 电脑网页响应；
- Dora 退出；
- CAN 超时。

断电后注意承接可能下落的机械臂。

## 9. 正常停止

结束操作时：

1. 控制器保持静止。
2. 可以先按 `B` 或在 UI 点击 `Arm Stop`。
3. 回到启动脚本的终端，按 `Ctrl+C`。
4. 等待左右 `robot` 节点完成 stop 和 disable。
5. 确认机械臂不再保持力矩后关闭机械臂电源。

不要直接关闭终端窗口，也不要只关闭 Quest Browser 后让数据流长期运行。

## 10. 重启后的状态

Ubuntu 重启后不会自动使能机械臂：

```text
Dora 未运行
8000/8080 未监听
can0/can1 为 DOWN
pxy111 自动跟随上游 Wi-Fi 信道
Quest 接入 USB 后自动恢复 ADB reverse
```

因此每次重启后仍需手动运行实机启动脚本，并输入 `START` 才会使能和移动双臂：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_webxr_real_pedestal.sh
```

不得把实机启动脚本设置为开机自启。

检查自动维护服务：

```bash
systemctl status openarm-webxr-hotspot.service
systemctl --user status openarm-webxr-adb-reverse.service
```

## 11. 已验证证据

截至 2026-11-07：

- 双路 PCAN 兼容 CAN-FD 适配器已识别。
- 右臂 `can0`、左臂 `can1` 的 ID 1–8 均正常响应。
- 双臂官方全零轨迹成功。
- 双臂官方 pedestal 初始姿态轨迹成功。
- 右臂 WebXR 对齐和小幅手柄跟随已初步跑通；左臂仍待验收。
- CAN 总线测试期间无 bus-off、error-passive 或错误帧。

未完成：

- 新 VR B/A/X 控制的修改后实机验收。
- 可达性、碰撞、奇异位形和 FK 残差硬门限。
- 真正的 clutch/deadman 机制。
- 长时间运行和断流恢复验收。
