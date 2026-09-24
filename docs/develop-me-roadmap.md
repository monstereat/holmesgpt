# AI 智能运维与故障诊断平台：二次开发路线图

> 仓库：`monstereat/holmesgpt`｜固定开发分支：`develop-me`｜更新：2026-09-25
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

## 1.1 本仓库后端技术栈

| 层 | 当前/建议技术 | 本仓库职责与边界 |
| --- | --- | --- |
| AI 调查核心 | Python、HolmesGPT、FastAPI/Uvicorn 及现有工具集 | 保留开源调查 Agent、模型与观测平台集成；不以 NestJS 重写 Holmes 推理。 |
| 示例业务服务 | TypeScript、NestJS、OpenTelemetry | `examples/openobserve-aiops/demo/order-service` 是本地订单故障注入和遥测演示，不代表已建成生产业务 API。 |
| 观测存储与检索 | OpenObserve | 承接日志、Trace、前端错误流和告警；当前 Docker Compose 仅供本机演示。 |
| 调查触发器 | 当前为 Python 标准库 HTTP Server；生产候选为持久队列/任务服务 | 当前仅内存去重、并发限制与 CLI 调用，不保证重启后任务恢复。 |
| 事故与审批服务 | NestJS + PostgreSQL（规划中） | 用于事故状态机、RBAC、审批和审计；尚未实现，数据库 schema/migration 需先经用户授权。 |
| 缓存/队列 | Redis（规划中，非当前依赖） | 如实现可靠重试与持久任务，先评估队列需求；当前内存线程不是可靠队列。 |

因此，本仓库实际代码以 Python 为 AI 后端，NestJS 仅用于 OpenObserve 演示业务服务。生产事故管理的推荐扩展是 NestJS + PostgreSQL；目前尚无生产 NestJS 事故中心、BullMQ 或 Redis 任务队列。

## 2. P0：先完成可重复演示的故障闭环

- [x] Demo 前端 SDK 上报异常并传递 Trace ID；实现字段脱敏与采样（Node 单测通过，浏览器 HTTP 500 实测通过）。
- [x] Demo NestJS 服务通过 OpenTelemetry 上报 Trace 与应用日志到 OpenObserve（本地服务端到端已验）。
- [x] 本地配置订单错误趋势仪表盘、500 Trace 告警和带鉴权的调查触发 Webhook；真实 Holmes 调用仍待验证。
- [x] 本仓库 `holmes/plugins/toolsets/openobserve/openobserve.py`：增加只读 OpenObserve Toolset、日志流发现、限制返回行数、显式时间范围及 SQL 基础校验。
- [x] 增加按 32 位 Trace ID 查询日志的工具、最长查询时间窗口、禁止带凭据的重定向。
- [x] `holmes/plugins/toolsets/__init__.py` 已注册新工具集；已提交 Mock API / 查询与参数校验测试代码。
- [x] `.github/workflows/develop-me-aiops.yml` 已提交独立工具集测试工作流定义。
- [x] 本地 OpenObserve 验证：日志、前端错误流、Trace 均收到相同 Trace ID；生产凭据、RBAC 和查询兼容性仍待验。
- [x] 浏览器 SDK → NestJS HTTP 500 → `app_logs` / `frontend_errors` / OpenTelemetry Trace 使用同一 Trace ID（真实浏览器 + 本机 OpenObserve 已验）。
- [ ] 告警调用真实 Holmes CLI 尚未验证；当前触发器用 `/bin/echo` 替身实测接收 Webhook 和 Trace ID。
- [x] 建立可回放的订单 HTTP 500 故障注入；[ ] 调查录像。

## 3. P1：展示工程可靠性的功能

- [ ] **真实发布关联：** 接通 Git/CI 发布 Webhook，按故障时间窗口检索并验证最新版本、提交和变更文件。
- [x] **本地发布事件原型：** NestJS 接受标准化发布事件，要求 HMAC-SHA256 签名并将版本/commit/变更文件写入 `app_logs`；调查提示要求按告警时间检索。Docker → OpenObserve 查询端到端已验；尚未接入 GitHub/GitLab/Jenkins。
- [x] **事故流程内存原型：** `examples/openobserve-aiops/incident_workflow.py` 实现状态转换、负责人、严重级别、幂等键、重复告警归并、审批事件和时间线；不执行处置命令。
- [ ] **持久化事故中心：** PostgreSQL 持久化、服务端身份/RBAC、重启恢复、并发幂等与审计查询（schema/migration 需先获得授权）。
- [x] **内存 RCA 数据模型：** 已验证结论必须带 HTTPS 证据链接，未验证结论明确标为 assumption；原型只记录声明，不自动验证其真实性。
- [ ] **真实 RCA 集成：** Holmes 输出逐项绑定 OpenObserve 日志/Trace、发布或 Runbook 来源；证据不足的结论保留为假设并可人工纠错。
- [x] **复盘草稿原型：** 从已记录时间线和有证据结论生成复盘草稿；没有证据的根因、影响范围、恢复时间和长期改进项明确留空待人工补充（12 项流程测试通过）。
- [x] **内存审批原型：** Agent 只能提交白名单建议，审批经外部授权回调验证；原型不执行运维命令。
- [ ] **生产受控处置：** 独立执行器须重新校验用户身份、授权、动作白名单、幂等键、取消和审计；高风险生产回滚需有验证失败回退。
- [ ] **完整复盘报告：** 根因、影响范围、发现与恢复时间、处理步骤、长期改进项均需真实事件数据和人工复核；当前仅有内存态草稿原型。
- [ ] **统一追踪：** 告警 ID / 调查任务 ID / Trace ID / 发布版本双向查询。
- [x] **本机任务查询原型：** 告警响应返回 task ID；同一 token 认证的 `GET /tasks/{task_id}` 返回状态、Trace ID 和结果，内存最多保留 1000 条/1 小时。未覆盖告警 ID、发布检索双向接口，且无持久化。
- [ ] **配置安全：** OpenObserve 使用最低权限的独立服务账户；日志查询限制流、时间、数量和查询耗时；对 SQL 策略做安全复核。

## 4. P2：后续扩展

- [ ] 建立不少于 20 个已知根因的可复现故障案例，测评检索覆盖率、诊断质量和误处置率。
- [x] 本机接收器 5 分钟进程内去重：带 Trace 告警按告警名和 Trace ID 做幂等键，无 Trace 告警排除触发时间但保留计数差异（9 项接收器测试通过）。
- [ ] Keep 集成或相似故障聚类；跨多个服务的共同根因分析。
- [ ] 审批后可执行的运维动作模板库及验证失败回退策略。

## 5. 首个演示场景与验收

**场景：** 一个订单接口的新版本造成 HTTP 500。前端 SDK 捕获错误并传递 Trace ID；NestJS 日志和 Trace 入库，OpenObserve 告警；HolmesGPT 通过自定义工具检索同一 Trace、最近发布和历史处理手册；输出附证据的诊断，发起人工审批，完成可回滚处置后验证错误率恢复，生成复盘。

**验收标准：** 既要证明正确找到前后端同一业务请求，也要证明无凭据或越权时无法搜索；调查失败可重试；未批准不得执行生产处置；报告包含真实证据链接与可核对的时间线。

### 本仓库现有代码入口

- `holmes/plugins/toolsets/openobserve/openobserve.py`：OpenObserve 工具集和 Trace 检索。
- `holmes/plugins/toolsets/openobserve/README.md`：Toolset 配置。
- `examples/openobserve-aiops/README.md`：基础接入示例。
- `examples/openobserve-aiops/incident_workflow.py`：仅用于测试和设计讨论的进程内审批状态机，不提供生产持久化或处置执行。
- `tests/plugins/toolsets/openobserve/`、`tests/toolsets/test_openobserve_toolset.py`：测试代码。
- `.github/workflows/develop-me-aiops.yml`：专用 CI 工作流配置。

```bash
poetry install --with dev
poetry run pytest -q tests/plugins/toolsets/openobserve tests/toolsets/test_openobserve_toolset.py
```

**状态说明：** [x] 表示文件已提交，不等于 CI 已成功或线上接入已完成。当前工具集的 SQL 文本拦截是基础防线，正式接生产前需结合只读账户、可访问流白名单、SQL 语法验证、查询配额和部署级网络隔离进一步加固。

## 2026-09-24 增量：OpenObserve 严格范围及凭据保护

- [x] 新增 `allowed_streams` 配置；配置后只允许单一白名单流的简单 SELECT 查询，拒绝 JOIN、UNION、子查询、CTE 和未授权流。
- [x] Trace 查询工具和日志流发现同样尊重白名单；日志流列表最多返回 100 条。
- [x] 上游 HTTP 错误消息脱敏；Basic Auth 请求不跟随重定向。
- [x] `tests/plugins/toolsets/openobserve/test_stream_scope.py` 覆盖越权流、复杂 SQL、Trace 越权和上游敏感错误响应。
- [x] `holmes/plugins/toolsets/openobserve/SECURITY.md` 记录生产最小权限要求；最近的专用 GitHub Actions 检查已通过。
- [ ] **生产接入仍待完成：** 服务端只读账户和流级 RBAC、真实 OpenObserve 集成、真实告警触发/审批/执行器；未配置白名单时保留开发模式，禁止将其当作生产安全保证。

## 2026-09-25 增量：本地 OpenObserve AIOps 演示

- [x] 新增 Docker Compose 本地 OpenObserve + NestJS 订单服务、故障注入、前端遥测 SDK 和本机告警接收器示例。
- [x] 增加签名发布事件入口、发布元数据白名单/长度限制，并让调查提示按告警时间检索版本、commit 和变更文件；真实 CI 平台 Webhook 尚待接入。
- [x] 修正 OTLP HTTP Trace 接收路径与 stream-name；固定 OpenObserve 镜像版本，增加服务启动等待、Webhook 本地 SSRF 限制说明。
- [x] SDK 脱敏/采样、告警 JSON 解析、鉴权、并发上限与重复告警过滤均有本地测试。
- [x] 本地容器验证 OpenObserve health、订单服务启动、HTTP 500 故障注入及日志/前端错误/Trace 的 Trace ID 关联。
- [x] 本地 UI 配置订单错误趋势仪表盘、告警模板、Webhook 目标和 SQL 告警；实际评估成功触发，接收器收到 `trace_id` 并异步启动调查命令。
- [x] 事故内存原型补充负责人、严重级别、幂等去重、证据/假设分类、恢复时间、URL 凭据参数脱敏和不臆造信息的复盘草稿；`test_incident_workflow.py` 12 项通过。
- [ ] 当前触发验证用 `/bin/echo` 替代 Holmes CLI；真实 Holmes 安装、OpenObserve 只读凭据与真实调查结果未验证。
- [ ] 调查录像、持久化事故状态机、生产审批和处置执行器仍未完成；其中数据库 schema/迁移需先获得用户授权。

**最近验证（2026-09-25）：** 订单服务 `npm test`（11 passed）、`npm run build`、告警接收器 pytest（9 passed）、事故流程 pytest（12 passed）、OpenObserve Toolset 定向 pytest（56 passed，使用 `--no-cov`；不加参数时这组子集触发全仓覆盖率门槛，15.31% 未达标）、Compose 配置检查和 `git diff --check` 均通过。真实浏览器点击订单得到 HTTP 500；OpenObserve 的 `app_logs`、`frontend_errors` 和 Trace 均命中同一 Trace ID。Docker 中用 `RELEASE_VERSION=v1.0.1` 注入 HTTP 500，再发送签名发布事件；OpenObserve 查询同时命中错误记录和发布事件，且 release 版本一致；无签名请求返回 401。SQL 告警实际触发本机接收器。真实 Holmes 调查仍未运行，当前回放以 `/bin/echo` 替代 Holmes CLI。

## 2026-09-25 增量：事故原型和告警输入边界

- [x] 事故内存原型记录 owner、severity、idempotency key、重复告警事件、审批决策、证据/假设分类及恢复时间；证据 URL 会剔除片段并脱敏常见凭据查询参数；复盘草稿明确区分已验证内容与待补字段；12 项流程测试通过。
- [x] Webhook 仅把数值计数和合法 ISO 时间作为摘要交给 Holmes，明确告警名/元数据为不可信数据，不把 CLI stderr 写入任务日志；9 项测试通过。
- [x] 5 分钟去重身份不受 Trace 告警的计数/触发时间变化影响；无 Trace 告警仅忽略触发时间，计数仍影响身份。
- [x] 通过认证的进程内任务查询可按 task ID 查看调查状态与 Trace ID；完成结果有数量/时长上限，9 项测试通过。
- [x] 发布事件入口校验原始请求体 HMAC-SHA256，拒绝缺少/错误签名、未知字段不入库、限制文件数和字段长度；实际演示事件按 release/commit 在 OpenObserve 命中。
- [x] 在 loopback 绑定的本机接收器上实测 Docker 网络 Webhook 返回 HTTP 202，`/bin/echo` 完成调度并保留 Trace ID。
- [x] 修复 `GET /monitoring.js` 静态 SDK 路由；真实浏览器产生 HTTP 500 后，本机 OpenObserve 三种数据均以同一 Trace ID 命中。
- [ ] 上述状态仍为内存原型，未具备服务端身份/RBAC、持久任务存储、进程重启恢复和生产执行器。
