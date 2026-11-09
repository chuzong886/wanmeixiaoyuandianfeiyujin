# 天枢能源管理系统 v1.0.0

本分支新增的天枢系统源码位于 [`tianshu-energy/`](tianshu-energy/)。

天枢是一套以用电空间为中心的预测、预警、设备接入与处理闭环系统，适用于家庭、宿舍、门店、办公室和厂房等场景。主要能力包括：

- 七日用量预测与波动区间；
- 数据完整度、时效和来源标识；
- 总表、分表、回路与设备拓扑去重；
- 设施与回路、智能电表和 HTTP 网关接入；
- 持续时间、滞回、恢复稳定和通知冷却规则；
- Webhook、飞书、SMTP 与系统内通知；
- 可配置 AI 分析接口；
- 操作审计、备份恢复和后端设置。

## 开始使用

详细说明请阅读：

- [`tianshu-energy/README.md`](tianshu-energy/README.md)
- [`tianshu-energy/docs/天枢引擎使用指南.md`](tianshu-energy/docs/天枢引擎使用指南.md)
- [`tianshu-energy/docs/网关接口说明.md`](tianshu-energy/docs/网关接口说明.md)

## 源码构建

前端位于 `tianshu-energy/frontend/`，后端位于 `tianshu-energy/backend/`。仓库不提交运行数据库、用户凭据、构建缓存和大型发布压缩包。

