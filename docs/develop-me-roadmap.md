# AI 智能运维与故障诊断平台：二次开发路线图

> 仓库：`monstereat/holmesgpt`｜固定开发分支：`develop-me`｜更新：2026-09-24  
> 开源底座：HolmesGPT；集成 OpenObserve、前端监控 SDK、NestJS、OpenTelemetry，Keep 按需加入。  
> 范围：围绕真实应用故障建立「采集 → 告警 → 调查 → 审批处置 → 验证 → 复盘」闭环；不自建日志数据库。

## 1. 功能边界

| 模块 | 开源项目已有能力 | 需要集成 | 自己开发的内容 | 优先级 |
| --- | --- | --- | --- | --- |
| 前端监控 | OpenObserve RUM、错误与性能数据接入 | 现有前端监控 SDK | SDK 上报适配、用户/路由/版本上下文及字段规范 | P0 |
| 后端监控 | 日志、Metrics、Trace | NestJS、OpenTelemetry | 订单等核心 API 埋点、异常归因和上下游 Trace 关联 | P0 |
| 监控大盘 | OpenObserve 查询、图表、看板 | SDK、业务指标与日志流 | 前端/后端/业务统一看板和告警视图 | P0 |
| 告警 | 基础阈值告警和通知 | OpenObserve 告警 Webhook | 业务严重程度、降噪策略和告警路由 | P0 |
| 故障调查 | HolmesGPT Agent、工具集、调查流程 | OpenObserve 查询 API | 自定义 OpenObserve Toolset、查询边界和调查上下文 | P0 |
| 日志关联 | 检索日志、调用链和字段 | 前端请求头与后端 Trace | 按 Trace ID 串联浏览器错误、NestJS 请求和服务日志 | P0 |
| 发布关联 | 工具可接外部部署事件 | GitLab / GitHub / Jenkins | 关联事故时间与版本、提交、发布批次 | P1 |
| 根因分析 | Agent 基于可用证据调查 | 链接日志/Trace/发布记录 | 证据链接、明确未知项、人工复核和纠错反馈 | P1 |
| 故障知识库 | 外部知识文档工具 | 内部 Runbook / 历史事故 | 文档检索、引用及事后沉淀 | P1 |
| 事故中心 | 可结合 Keep 或自建业务层 | NestJS、PostgreSQL | 故障工单、指派、分级、状态机和重复告警归并 | P1 |
| 自动处置 | HolmesGPT 工具调用机制 | 受控 CI/CD、运维平台 | 工具白名单、审批、幂等执行、失败回退与审计 | P1 |
| 故障复盘 | 调查结果和事件历史 | 发布记录、监控事件 | 自动整理时间线、根因及改进项 | P1 |
| 故障评测 | 基础调查能力 | 故障注入和评测数据 | 已知根因样本、诊断准确性、证据完整性和恢复时间 | P2 |

**产品限制：** OpenObserve 社区版不应默认按 Enterprise/Cloud 的完整事件管理或细粒度 RBAC 规划，缺失部分由自建 NestJS 事故中心或其他已授权工具补充。

## 2. P0：先完成可重复演示的故障闭环

- [ ] 前端 SDK 上报真实 JavaScript 异常及请求 Trace ID；明确脱敏和采样规则。
- [ ] NestJS 接入 OpenTelemetry，把 Trace 与应用日志发往 OpenObserve。
- [ ] 配置综合大盘、500 错误率告警与调查任务触发。
- [x] 本仓库 `holmes/plugins/toolsets/openobserve/openobserve.py`：增加只读 OpenObserve Toolset、日志流发现、限制返回行数、显式时间范围及 SQL 基础校验。
- [x] 增加按 32 位 Trace ID 查询日志的工具、最长查询时间窗口、禁止带凭据的重定向。
- [x] `holmes/plugins/toolsets/__init__.py` 已注册新工具集；已提交 Mock API / 查询与参数校验测试代码。
- [x] `.github/workflows/develop-me-aiops.yml` 已提交独立工具集测试工作流定义。
- [ ] 完成真实 OpenObserve 环境验证：凭据、查询语法兼容性、日志流和 Trace 命中情况。
- [ ] 真实前端 SDK / NestJS 接入及端到端告警→调查尚未验证。
- [ ] 建立可回放的订单 HTTP 500 故障注入和调查录像。

## 3. P1：展示工程可靠性的功能

- [ ] **发布关联：** Git/CI 发布 Webhook，按故障时间窗口检索最新版本、提交和变更文件。
- [ ] **事故任务状态机：** `detected → investigating → awaiting_approval → remediating → resolved / failed`；记录负责人、幂等键与时间线。
- [ ] **带证据的 RCA：** 每一项结论至少对应日志、调用链、发布或 Runbook 证据；无法验证时标记假设。
- [ ] **受控自动处置：** Agent 仅提交建议；由授权用户审批白名单操作；高风险生产回滚必须支持取消和审计。
- [ ] **复盘报告：** 根因、影响范围、发现与恢复时间、处理步骤、长期改进项。
- [ ] **统一追踪：** 告警 ID / 调查任务 ID / Trace ID / 发布版本双向查询。
- [ ] **配置安全：** OpenObserve 使用最低权限的独立服务账户；日志查询限制流、时间、数量和查询耗时；对 SQL 策略做安全复核。

## 4. P2：后续扩展

- [ ] 建立不少于 20 个已知根因的可复现故障案例，测评检索覆盖率、诊断质量和误处置率。
- [ ] Keep 告警降噪或自定义相似故障聚类；跨多个服务的共同根因分析。
- [ ] 审批后可执行的运维动作模板库及验证失败回退策略。

## 5. 首个演示场景与验收

**场景：** 一个订单接口的新版本造成 HTTP 500。前端 SDK 捕获错误并传递 Trace ID；NestJS 日志和 Trace 入库，OpenObserve 告警；HolmesGPT 通过自定义工具检索同一 Trace、最近发布和历史处理手册；输出附证据的诊断，发起人工审批，完成可回滚处置后验证错误率恢复，生成复盘。

**验收标准：** 既要证明正确找到前后端同一业务请求，也要证明无凭据或越权时无法搜索；调查失败可重试；未批准不得执行生产处置；报告包含真实证据链接与可核对的时间线。

### 本仓库现有代码入口

- `holmes/plugins/toolsets/openobserve/openobserve.py`：OpenObserve 工具集和 Trace 检索。
- `holmes/plugins/toolsets/openobserve/README.md`：Toolset 配置。
- `examples/openobserve-aiops/README.md`：基础接入示例。
- `tests/plugins/toolsets/openobserve/`、`tests/toolsets/test_openobserve_toolset.py`：测试代码。
- `.github/workflows/develop-me-aiops.yml`：专用 CI 工作流配置。

```bash
poetry install --with dev
poetry run pytest -q tests/plugins/toolsets/openobserve tests/toolsets/test_openobserve_toolset.py
```

**状态说明：** [x] 表示文件已提交，不等于 CI 已成功或线上接入已完成。当前工具集的 SQL 文本拦截是基础防线，正式接生产前需结合只读账户、可访问流白名单、SQL 语法验证、查询配额和部署级网络隔离进一步加固。
