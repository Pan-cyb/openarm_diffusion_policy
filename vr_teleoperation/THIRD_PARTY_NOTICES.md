# 第三方来源与改编说明

本目录复用和改编 Enactic 的 OpenArm 项目：

- 上游：`https://github.com/enactic/dora-openarm-data-collection`
- 固定根提交：`5be94fe9873b02d5123bedc96e07272ac0112c2d`
- WebXR 子模块：`abd6a1f7c18d3a0a8ca5e8d67c2c896d449bea86`
- 上游派生文件保留原有 Enactic 版权头和 Apache-2.0 许可声明。
- Apache-2.0 完整文本见 `third_party/APACHE-2.0.txt`。

`local/static/` 中的网页与 JavaScript 基于上游 WebXR 前端改编；
本地修改包括版本化入口、局域网 ICE、根路径信令、坐标轴/安全提示、
X 录制/成功结束与 B 停臂/失败门控。
`local/webxr_http_localhost.py` 是对固定上游服务的本地 HTTP/采集接口包装。
本地 YAML 为 V2 pedestal 数据流的派生配置，来源与差异在文件注释中说明。
其余本地相机、录制和测试适配的作用边界见相应 README。

本说明保留第三方来源与适用许可，不代表 Enactic 对本地改动、实机安全或
实验结论背书，也不替所有者为其他原创文件选择新的整体项目许可证。
官方 V2 MuJoCo 模型的来源和许可证单独保存在
`../openarm_v2/vendor/openarm_mujoco/SOURCE.json` 与该目录的 `LICENSE`。

本地运行还使用 libuvc、TurboJPEG 和官方 Dora SDK。
其源码/二进制不在本次提交中；固定版本、校验值和使用边界见相机与四路入口文档，
相应安装目录须保留各自的来源和许可证。
