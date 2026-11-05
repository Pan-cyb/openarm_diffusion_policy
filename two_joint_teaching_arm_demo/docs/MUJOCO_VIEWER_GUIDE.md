# MuJoCo Viewer 中文使用手册

适用环境：MuJoCo 3.6 Python Viewer，以及本项目的两关节示教机械臂。

## 1. 模型和状态

MuJoCo 将信息分成两类：

```text
mjModel：结构和固定参数
  关节、连杆、质量、限位、执行器、相机、求解器配置

mjData：当前时刻的状态
  qpos、qvel、ctrl、力、传感器值、仿真时间
```

Viewer 修改右侧 `Control` 时，主要改变 `mjData.ctrl`；物理引擎随后更新
`mjData.qpos` 和 `mjData.qvel`。

## 2. qpos 是什么

`qpos` 是 generalized position，即系统的广义位置向量。

不同关节在 `qpos` 中占用的内容不同：

| 关节类型 | `qpos` 内容 | 常见单位 |
|---|---|---|
| `hinge` | 一个转角 | rad |
| `slide` | 一个平移距离 | m |
| `ball` | 一个单位四元数 | 无量纲 |
| `free` | 三维位置加四元数 | m + 无量纲 |

本项目只有两个 hinge：

```text
qpos[0] = shoulder 当前实际角度，rad
qpos[1] = elbow 当前实际角度，rad
```

因此 Watch 面板中：

```text
Field = qpos
Index = 1
Value = -0.506943
```

表示第二个广义位置，也就是肘关节当前约为 `-0.507 rad`，约 `-29°`。
Viewer 的 Index 从 0 开始。

相关状态：

```text
qvel            广义速度
ctrl            执行器输入或目标
actuator_force  执行器实际输出力/力矩
sensordata      sensor 元素输出
xpos            body 世界坐标位置
```

## 3. Joint 和 Control 为什么不同

本项目使用 position actuator：

```text
Control = 想让关节到达的目标角度
Joint/qpos = 关节当前实际角度
```

例如：

```text
Control shoulder = 0.70 rad
Joint shoulder   = 0.63 rad
```

控制器会产生力矩让实际位置靠近目标，但两者不会永远完全相等。误差来自：

- 惯性和运动时间。
- 重力和外部负载。
- 阻尼与摩擦。
- 执行器力矩上限。
- 接触或碰撞约束。
- `kp/kv` 控制增益。
- 仿真刚启动或目标正在变化。

本项目的 `Control` 单位是 rad，因为它使用位置执行器。其他模型不一定相同：

```text
位置执行器 ctrl：通常是 rad 或 m
速度执行器 ctrl：通常是 rad/s 或 m/s
力矩执行器 ctrl：可能是 N·m
通用 motor ctrl：还可能经过 gear 缩放
```

直接拖动 `Joint` 相当于修改当前状态，接近“瞬移”；拖动 `Control` 才是给电机目标。
示教时使用 `Control`。

## 4. Keyframe 是什么

Keyframe 不是视频关键帧，而是一份保存好的仿真状态快照。

一个 keyframe 可以保存：

```text
time   仿真时间
qpos   位置
qvel   速度
act    执行器内部激活状态
ctrl   控制输入
mpos   mocap body 位置
mquat  mocap body 姿态
```

本项目定义：

```xml
<keyframe>
  <key name="home"
       qpos="0.25 -0.50"
       ctrl="0.25 -0.50"/>
</keyframe>
```

它表示肩关节 `0.25 rad`、肘关节 `-0.50 rad` 的 home 状态。

Simulation 面板中：

- `Key`：选择 keyframe 编号，编号按 XML 出现顺序从 0 开始。
- `Key = -1`：当前没有选择可加载的 keyframe。
- `Load key`：把所选 keyframe 复制到当前 `mjData`。
- `Save key`：把当前状态复制到所选 keyframe 的内存槽。
- `Save xml`：把当前编译模型，包括内存中的 keyframe，保存为 MJCF。
- `Reset`：恢复默认初始状态，不一定等于加载某个命名 keyframe。

## 5. 如何暂停

标准 managed Viewer：

```text
Space：Pause / Run
左侧 Simulation -> Pause / Run：相同作用
```

本项目使用 `mujoco.viewer.launch_passive()`，物理循环由 Python 掌管，所以标准
Pause/Run 可能变灰。项目已增加自己的暂停逻辑：

```text
Space：暂停或继续
```

暂停时不执行 `mj_step()`，只执行 `mj_forward()`刷新派生状态和画面。

本项目自定义按键：

```text
Space  暂停/继续
R      开始录制
S      停止并保存
P      回放已保存轨迹
H      回 home
```

修改代码后需要关闭旧 Viewer 并重新启动，旧进程不会自动获得新快捷键。

## 6. File

| 按钮 | 作用 |
|---|---|
| `Save xml` | 保存当前编译模型为 MJCF |
| `Save mjb` | 保存已编译的二进制模型 |
| `Print model` | 输出 `mjModel` 内部数据 |
| `Print data` | 输出当前 `mjData` 状态 |
| `Screenshot` | 保存当前画面 |
| `Quit` | 退出 Viewer |

保存后的 XML 可能是编译器展开后的模型，不应代替有注释、include 和版本控制的原始
手写文件。

## 7. Option

| 选项 | 作用 |
|---|---|
| `Help` | 显示 Viewer 快捷键 |
| `Info` | 显示模型尺寸、仿真速度等信息 |
| `Profiler` | 显示求解器和物理步骤耗时 |
| `Sensor` | 显示传感器输出 |
| `Pause update` | 暂停 Viewer 状态刷新，不等同于物理暂停 |
| `Fullscreen` | 全屏 |
| `Vertical Sync` | 垂直同步 |
| `Busy Wait` | 用更多 CPU 换取更紧凑的实时等待 |
| `Spacing` | UI 元素间距 |
| `Color` | UI 配色 |
| `Font` | UI 字体比例 |

## 8. Simulation

| 按钮 | 作用 |
|---|---|
| `Pause / Run` | 暂停或运行 managed Viewer |
| `Reset` | 重置数据 |
| `Reload` | 重新读取模型文件 |
| `Align` | 重新调整默认观察视角 |
| `Copy state` | 将当前状态复制成 `<key>` 文本 |
| `Key` | 选择 keyframe |
| `Load key` | 加载 keyframe |
| `Save key` | 保存到 keyframe 内存槽 |
| `Noise scale/rate` | 给控制量增加相关噪声 |
| `History` | 回看 managed Viewer 保存的历史状态 |

在 passive Viewer 中，部分 Simulation 控件可能不可用，因为 Python程序拥有时间推进权。

## 9. Watch

Watch 用于观察一个数组中的一个元素：

```text
Field：mjData 字段名
Index：扁平化后的索引
Value：只读实时数值
```

常用例子：

```text
qpos, index 0           肩关节位置
qpos, index 1           肘关节位置
qvel, index 0           肩关节速度
ctrl, index 0           肩关节控制目标
actuator_force, index 0 肩关节执行器力矩
```

复杂二维数组在这里按内存顺序展开，因此排查复杂模型时最好同时查看模型文档或通过
Python打印数组 shape。

## 10. Physics

Physics 面板会修改实际仿真参数，不只是画面：

- `Integrator`：数值积分器。
- `Timestep`：物理步长。
- `Solver`：约束求解器。
- `Iterations/Tolerance`：求解次数与停止阈值。
- `Gravity`：重力。
- `Wind/Density/Viscosity`：环境参数。
- `Contact Override`：覆盖接触软硬度、摩擦和 margin。
- `Actuator Group Enable`：真正启用或禁用执行器组。

初学阶段不要为了“让它能动”随意修改 Physics。应先检查：

1. Control 是否正确。
2. 执行器力矩是否饱和。
3. 关节方向和限位。
4. 是否发生碰撞。
5. 质量和重心是否合理。

## 11. Rendering

Rendering 只改变显示方式：

- `Camera`：Free、Tracking 或 XML 中的命名相机。
- `Label`：显示 body、joint、geom、site、camera 等名称。
- `Frame`：显示相应坐标轴。
- `Copy camera`：复制当前自由视角的 `<camera>` 参数。
- `Model Elements`：显示惯量、凸包、接触点和接触力等调试元素。
- `OpenGL Effects`：阴影、反射、透明和线框等效果。

## 12. Visualization

Visualization 主要调整画面参数：

- Headlight 的环境光、漫反射和镜面反射。
- 自由相机的正交/透视模式。
- `Field of view`。
- `Center`、`Azimuth`、`Elevation`。
- 全局显示尺寸和各种可视化缩放。

这些选项通常不会修改机械臂动力学。

## 13. Group enable

Group enable 是渲染可见性过滤器，不是物理开关。

MuJoCo元素可以在 XML 中设置：

```xml
<geom group="3"/>
<site group="1"/>
```

Viewer按 0～5 分组显示：

```text
紫色/亮色：该组可见
黑色/暗色：该组隐藏
```

各区域：

- `Geom groups`：几何体。
- `Site groups`：site 标记。
- `Joint groups`：关节可视标记。
- `Tendon groups`：腱。
- `Actuator groups`：执行器可视标记。
- `Flex groups`：柔性体。

关闭 `Geom 3` 只会隐藏 group 3 的几何体，不会关闭碰撞。OpenArm等模型通常把
视觉网格和碰撞网格放在不同 group，打开碰撞组可以检查简化碰撞体。

不要混淆：

```text
Group enable -> 只影响 Viewer 显示
Physics / Actuator Group Enable -> 可能影响实际执行器物理
```

## 14. 标准快捷键

```text
Space          Pause / Run
+ / -          加速 / 减速
左 / 右方向键  单步后退 / 前进
Tab            显示或隐藏左侧 UI
Shift+Tab      显示或隐藏右侧 UI
[ / ]          循环固定相机
Esc            自由相机
F1             Help
F2             Info
F3             Profiler
F4             Sensor
F5             全屏
F6             循环 Frame
F7             循环 Label
```

鼠标：

```text
左键拖动              旋转视角
右键拖动              平移视角
滚轮或中键拖动        缩放
双击                  选择物体
右键双击              相机中心对准物体
Ctrl+右键双击         跟踪物体
Ctrl+拖动             对动态物体施加扰动
```

## 15. 常见误区

- `qpos` 不总是“关节角”；slide 是米，free/ball 包含四元数。
- `ctrl` 不总是位置；单位取决于 actuator 类型和 transmission。
- `Joint != Control` 是正常现象，不一定是故障。
- Keyframe 是状态快照，不是录像帧。
- 隐藏 geom group 不会关闭碰撞。
- 暂停 Viewer 刷新不等于暂停物理。
- 直接拖 Joint 是修改状态，不是通过电机运动。
- 仿真中的 `kp/kv` 不应直接照搬到实物电机固件。
