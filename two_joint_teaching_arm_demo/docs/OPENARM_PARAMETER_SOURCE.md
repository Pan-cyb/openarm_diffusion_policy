# OpenArm 参数来源

本项目只复用 OpenArm v1 MuJoCo 模型中 `motor_DM4310` 的仿真参数，不复制
OpenArm 的机械结构。

来源：

```text
repository: https://github.com/enactic/openarm_mujoco
commit: a3c8a57ec15d385e3c1dbd3fd090ee486b7f258c
file: v1/openarm_bimanual.xml
```

对应内容：

```xml
<default class="motor_DM4310">
  <joint frictionloss="0.1"
         damping="0.9"
         armature="0.002"
         limited="true"
         type="hinge" />
  <position forcelimited="true"
            forcerange="-7 7"
            ctrllimited="true" />
</default>
```

OpenArm 对 joint5、joint6 和 joint7 使用的位置执行器增益为：

```xml
kp="30" kv="1.5"
```

上游注释明确指出，转子惯量和阻尼是基于电机读数及减速比的近似，减速箱贡献尚未
确认。它们适合复现官方仿真行为，但不应被当作 DM4310 实物辨识结果。

达妙官方 DM-J4310-2EC 数据表给出的硬件级信息包括 10:1 减速比、24 V 额定电压、
120 rpm 额定转速、3 N·m 额定力矩和 7 N·m 峰值力矩。实际采购型号、固件和控制
协议仍需以手头电机的铭牌及对应手册为准。
