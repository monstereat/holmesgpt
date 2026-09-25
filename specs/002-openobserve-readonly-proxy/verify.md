# T008 验证记录

日期：2026-09-25
目标环境：当前电脑 Docker Compose 测试环境
结果：策略代理和网络隔离验证通过；真实模型调查仍需 DeepSeek API Key。

- [x] 代理镜像构建通过，11 项 `unittest` 通过。覆盖 allowlist 查询、未知流/database qualifier、JOIN/UNION/子查询/外部表函数/多语句/注释/匿名函数拒绝、时间窗/未来时间/timeout 检查、认证、路由、流过滤、行数 clamp 和上游错误脱敏。
- [x] `docker compose config --quiet` 与 `docker compose up -d --build` 通过。proxy、Holmes、incident API/worker、OpenObserve、order-service、PostgreSQL 与 Redis 均启动；Holmes、proxy、incident API、order-service、OpenObserve health endpoint 正常。
- [x] `docker inspect` 网络核验：Holmes 与 OpenObserve 没有共享 Docker network；Holmes 通过 DNS 无法解析 `openobserve`；proxy 同时加入 observe-policy 与 holmes-observe 两张网络。
- [x] Holmes 容器内仓库原生 `OpenObserveToolset.prerequisites_callable` 经独立代理凭据成功发现 2 条 allowlist 流。
- [x] 本机 order-service 写入一条合成订单日志并 flush；Holmes 容器内使用原生 Toolset 搜索近 5 分钟 `app_logs`，通过代理查询到 1 条对应真实 OpenObserve 记录。测试没有输出日志内容。
- [ ] DeepSeek API Key 尚未提供，没有发起 LLM 调用或通过真实告警触发 RCA；本任务验证不代表 AC-04 live Holmes 调查验收完成。
- 范围门禁：`specs/002-openobserve-readonly-proxy/scope-report-T008.json`，通过。
