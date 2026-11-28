# Quest WebXR 遥操作交接

## 当前交接（2026-11-28）

19:10 会话已执行旧版两段轨迹，但因左 J7 末端偏差双臂停止。新会话 `wide-40mm-direct-v2` 直接去宽起点，小幅末端偏差可在确认双侧失能后由操作者重新长按 A。当前命令及适用边界以[操作手册](数据采集操作流程.md)为准，以下为历史。

18:44 和 18:55 两次启动均在展开前中止，双臂未到宽起点。后一次为右臂 CAN 发送缓冲不足，两臂均未派发位置指令。**暂停采集，先由现场确认双臂安全状态。** 故障与本地修订见[项目状态顶部](PROJECT_STATUS.md)。以下为原定的试采流程，不是立即重试指令。见
[挂脖四路采集、原 PD 与首帧重力前馈](handoff/2026-11-28_1656_capture_pdff.md)、
[现场操作步骤](数据采集操作流程.md)。

## 历史交接（2026-11-27）

DP 真机保持误差、修改前快照、候选配置、离线验收与接续条件见
[V2 pedestal PD 与重力前馈交接](handoff/2026-11-27_2005_pedestal_pdff.md)。
现场采集与实际 DP 运动仍使用原始 pedestal 基线；新入口仅做 MuJoCo 对照。

## 采集主线历史（2026-11-21）

- 正常照明已确认，四路真实相机＋合成关节约 30 秒完整录制再次通过。
  最新 `diagnostics/20261121_mainline_rows900`：四路约 29.91 fps，3559 张 JPEG
  解码/哈希/发布集合/起止边界通过；四组关节各 900 行，不是任务示范。
- 85°C 已由停止改为日志提醒；当前 6800H 以 AMD 95°C Tjmax 为参考保留软件停止，
  内核故障、掉帧/积压检查仍保留。本轮峰值 80.25°C，正常落盘退出，八接口恢复。
- 174 项采集/相机测试、4 项 HTTP、VR 按键回归和 shell 语法通过。
  新四路会话统一独立官方 Dora 1.1.0，三路无损 180°、胸部原 JPEG，不改官方控制参数。
  真实控制运行时尚未现场复验，不以模拟关节证明真机安全。
- 当前命令与步骤见 `数据采集操作流程.md`，不再使用旧 three_camera 真实启动命令。
  下一步只做现场一条完整试采并回放，不追加优化、强制 120 秒压测或排查历史故障。
- 本轮未打开 CAN/串口、未启动机器人、未改 BIOS/功耗/风扇/接线、未重启。
  采集进程已退出，临时 sudo 授权已撤销。后续真机操作仍需明确现场授权。
- 当前状态以 `PROJECT_STATUS.md` 顶部为准；以下旧记录不覆盖最新结果。

## 历史记录（2026-11-20 夜间）

- 后续新增近黑门控，真实入口在 `ROBOT` 输入前后及真实桥接开始/记录中检查；
  诊断仍允许暗场。9 项新回归及保存真实图离线回放拒绝通过，新版 mock 完整读回通过。
  当前 four_camera 37 项，合计 140 项 Python；没有重新打开硬件，
  正常场景新增开销仍待实测。`basic_image_ready` 不等于完整画质验收。
- 继续用户的四路真实采集目标，不自动扩大到机器人、整机重启、驱动/BIOS/供电。
- 新入口 `../local/four_camera/README.md`；验收清单 `四路相机采集验收.md`。
  已完成四路服务、raw drain、Dora/Recorder/global 接入、只读恢复工具；
  真实相机＋合成关节诊断已经落盘和读回，不是任务示范。
- 暗场关动态帧率约 30 fps，原开关为 1 时约 10 fps；画面接近黑色。
  已问现场照明/遮挡，尚未确认，下一步不能拿暗场结果宣称正常采集验收。
- 服务 SIGKILL 后不恢复接口的真实失败已修，native_guard 后重开通过；
  原机卡死根因、整机重启恢复、正常画面长测未通过。四路参数已恢复原值。
- 131 项 Python 与 6 组原生离线测试通过。最新真实诊断每路 30–31 JPEG，
  四组合成关节各 30 行，原始证据和失败目录均保留。
- 最新证据 `../local/camera_bandwidth/evidence/2026-11-20-commissioning/README.md`；
  `PROJECT_STATUS.md` 顶部覆盖以下历史“未接入四路”的结论。

## 最新入口（2026-11-20）

- 最新离线改善已增加原子 JPEG/metadata 写入和收尾 fsync 后保存确认；
  94 项 Python 测试、Recorder 合成落盘通过，真实吞吐/重启恢复仍未验收。
- 偏暗预览是启动第一帧，已改用约 2 秒后帧，无后期拉亮；
  不等于曝光质量已通过，见证据 README 的“同日离线改善”。
- 当前四路微雪用户映射：2.2 右腕、2.3 左腕、2.4 胸部、1 全局；
  双腕和全局旋转 180°，胸部不旋转。配置：
  `../local/camera_bandwidth/four_camera_layout.json`。
- 1600 四路启动因带宽不足失败；1280 可短时并发但约 20–25 fps，有序号缺口。
- 主机卡死后 22:30:37 重启；先前有 CPU/L1 已纠正机器检查记录，因果未定。
  重启后 511 张读回发现 1 张胸部 0 字节，不能判正式采集通过。
- 没有新取流或机器人操作；先处理主机/落盘稳定性。旧三相机 DJI 后端未迁移四路，
  不运行旧真实入口。证据见
  `../local/camera_bandwidth/evidence/2026-11-20-four-camera/README.md`。
- 本次交接：`handoff/2026-11-20_four_camera_handoff.md`。

## 最新入口（2026-11-16）

- 最新软件流程：`数据采集操作流程.md`；本轮交接：
  `handoff/2026-11-16_capture_workflow_handoff.md`。
- 裸 8080 自动跳内容版本路径，X 录制/成功，B 停臂/失败，A 不录制；
  三相机入口要求双臂 ALIGNED、新鲜输入和 Recorder 确认，默认写已核实 Expansion。
  以上仅离线/mock 验证，Quest 显示和实机采集未验收。
- 当前为三相机软件/mock 接入与独立相机预览；正式采集、训练和自主执行未启动。
- 本轮已获单次相机读取授权并检查真实相机，但未打开 CAN/串口或发送机械臂指令；
  恢复工作时仍默认离线/mock，不继承此前设备访问授权。
- 接入说明：`../local/three_camera/README.md`。
- 抽屉任务采集方案：`抽屉放方块_数据采集方案.md`。
- 软件/mock 验证摘要：`../local/three_camera/mock_verification.json`。
- 微雪双腕临时样图已旋转 180 度，端口 `2.2` 单路视频预览已展示并由用户初步认可。
  不是仅上下翻转；后续采集软件入缓存前已接入 `rot180-v1`，真实保存/训练/推理仍待验收。
- 左右腕物理端口归属仍未确认。大疆已确认安装在机器人胸前，
  但本轮机身显示 Webcam 后未恢复可用 UVC 节点，胸部画面仍未检查。
- 候选 `05a3:9230` 全局相机曾成功取得 1080p 原图，未发现公开无畸变开关；
  镜头/品牌和私有扩展功能未知，不把单帧外观当成畸变标定。
- 用户计划再购两台微雪作外部视角候选，尚未确认到货或最终五路配置。
- 代表性截图：`evidence/2026-11-16-camera-preview/README.md`。
- 较早相机现场记录：`handoff/2026-11-16_2108_handoff.md`；
  收尾未检测到预览或控制/采集进程，不等于确认机械臂失能。
- 老师建议先仿真，具体交付目标待确认；不自动安装 Isaac、启动训练或扩展相机管线。
- 新实机后端、时序及碰撞/持续受阻/温度联锁仍未验收，详见状态文档。
- 下面的早期仿真恢复段保留作历史，不应覆盖 `PROJECT_STATUS.md` 中的较新状态。

## 入口

- 工作区：`/home/pan/openarm_diffusion_policy`
- 项目：`vr_teleoperation/`
- 技术说明：`docs/WEBXR_TELEOPERATION.md`
- 状态：`docs/PROJECT_STATUS.md`
- 本地运行入口：`local/run_webxr_pedestal.sh`

## 当前结论

- WebXR immersive 是当前主线。
- 开发入口采用 USB ADB reverse 和 `http://localhost:8080`。
- Quest 已成功进入 WebXR 会话。
- 头显内粗 RGB 轴已确认可见，显示原始手柄 aim frame。
- UDP controller-only 已跑通到 pedestal MuJoCo，但当前暂停，仅作为坑位保留。
- 桌面 MuJoCo 使用正确的 V2 pedestal，并显示映射后的目标 frame。
- WebXR 主线必须正常佩戴头显，不使用挂脖子操作。
- 本轮三相机接入及后续相机预览没有真机 CAN 通信或机械臂运动指令；
  更早的实机验证记录见 `PROJECT_STATUS.md`，
  不能再用早期“没有真机证据”的仿真结论概括整个项目。

## 固定来源

```text
enactic/dora-openarm-data-collection
5be94fe9873b02d5123bedc96e07272ac0112c2d
```

上游目录：

```text
vr_teleoperation/upstream/dora-openarm-data-collection/
```

固定模型：

```text
openarm_v2/vendor/openarm_mujoco/v2/pedestal/pedestal.xml
```

## 恢复步骤

1. 读取 `AGENTS.md`、`vr_teleoperation/AGENTS.md`。
2. 读取本文件、`docs/PROJECT_STATUS.md` 和技术说明。
3. 确认 Quest 连接热点和 USB 调试。
4. 运行 `local/run_webxr_pedestal.sh`。
5. Quest 浏览器打开 `http://localhost:8080/` 并点击 `Start`。
6. 从小幅逐轴仿真验收继续，不连接真机。

UDP 坑位保留入口：

```bash
cd /home/pan/openarm_diffusion_policy/vr_teleoperation/local
bash run_udp_pedestal.sh
```

未得到新的明确恢复要求前，不继续该入口。
