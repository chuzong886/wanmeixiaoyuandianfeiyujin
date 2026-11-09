# 第三方组件

- Vue 3.5.21：MIT，https://github.com/vuejs/core
- Apache ECharts 6.0.0：Apache-2.0，https://github.com/apache/echarts
- ZRender 6.0.0：BSD-3-Clause，https://github.com/ecomfe/zrender
- Microsoft Windows App SDK 2.4.0 / WinUI：发行包中的许可证与NOTICE，https://github.com/microsoft/WindowsAppSDK
- .NET 8：运行时发行包中的许可证，https://github.com/dotnet/runtime
- Python 3.12.10 Windows embeddable：官方发行包中的LICENSE，https://www.python.org/ftp/python/3.12.10/
- SQLite：Python发行包提供的SQLite运行库；https://www.sqlite.org/copyright.html

复用原 campus-local 的 CampusClient、Capture、Forecast 代码作为设备协议适配器；未复制旧数据库或用户配置。

- Java 私有运行时：与原本地版一致，许可证和 NOTICE 位于 api-runtime/runtime/legal/。
- org.json 20240303：Public Domain（以 JAR 内 Maven 元数据为准），许可证随 licenses/api/ 提供。

许可证文本随运行包的 licenses/ 目录及对应运行时附带文件提供。


0.3.0 前端采用 [Art Design Pro](https://github.com/Daymychen/art-design-pro)，上游提交 f3aaf58eec1a0e988f162352c33862327a484f95，MIT；许可证在 licenses/Art-Design-Pro-MIT.txt，集成记录在 frontend/src/art/UPSTREAM.md。复用官方核心菜单、主题状态、日夜主题卡片、配色选择组件、语言资源与样式，并针对本机能源业务适配路由、会话和偏好持久化。上游示例认证接口和演示页面未启用。

新增前端组件包括 Element Plus、Vue Router、Pinia、Vue I18n、VueUse、Iconify、Tailwind CSS，版本锁定于 frontend/package-lock.json；许可证文本保存在 licenses/web/。
