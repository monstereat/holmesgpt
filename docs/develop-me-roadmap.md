# AI 智能运维与故障诊断平台：二次开发路线图

> 仓库：`monstereat/holmesgpt`｜固定开发分支：`develop-me`｜更新：2026-09-25
> 开源底座：HolmesGPT；集成 OpenObserve、前端监控 SDK、NestJS、OpenTelemetry，Keep 按需加入。  
> 范围：先在当前电脑 Docker 测试环境围绕真实应用故障建立「采集 → 告警 → 调查 → 审批处置 → 验证 → 复盘」闭环；正式环境部署后续另行规划和授权；不自建日志数据库。

> **当前验收状态（2026-09-25）：** T000–T009 的本机实现和测试环境编排已落地，事故 API、worker、Holmes API、PostgreSQL、Redis、OpenObserve 与订单服务正在同一个 Docker Compose project 运行。重启后事故/任务数据仍在，隔离测试库恢复成功；测试动作完成审批、执行和回滚验证，事故复盘已持久化并提供审核表单。Holmes API 健康不代表 Agent 调查通过：当前没有模型凭据，最近一次真实告警任务以 `holmes_unavailable` 安全失败，故 AC-04 与完整 live 验收仍未完成。没有部署到生产环境。

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
| 故障复盘 | 调查结果和事件历史 | 发布记录、监控事件 | 持久化复盘、审核状态、改进项与审计 | P1 |
| 故障评测 | 基础调查能力 | 故障注入和评测数据 | 已知根因样本、诊断准确性、证据完整性和恢复时间 | P2 |

**产品限制：** OpenObserve 社区版不应默认按 Enterprise/Cloud 的完整事件管理或细粒度 RBAC 规划，缺失部分由自建 NestJS 事故中心或其他已授权工具补充。

## 1.1 本仓库后端技术栈

| 层 | 当前/建议技术 | 本仓库职责与边界 |
| --- | --- | --- |
| AI 调查核心 | Python、HolmesGPT、FastAPI/Uvicorn 及现有工具集 | 保留开源调查 Agent、模型与观测平台集成；不以 NestJS 重写 Holmes 推理。 |
| 示例业务服务 | TypeScript、NestJS、OpenTelemetry | `examples/openobserve-aiops/demo/order-service` 是本地订单故障注入和遥测演示，不代表已建成生产业务 API。 |
| 观测存储与检索 | OpenObserve | 承接日志、Trace、前端错误流和告警；当前 Docker Compose 仅供本机演示。 |
| 调查触发器 | Python/FastAPI 事故 API、Celery worker、Redis broker | Webhook 事务写入事故/任务/outbox；worker 有界重试、租约回收及 outbox 重投。 |
| 事故与审批服务 | Python/FastAPI + PostgreSQL | 本仓库已实现事故/任务持久化、测试身份与 RBAC、审批、审计、重启恢复和只限 demo order-service 的测试动作。schema 只应用于用户授权的本机测试数据库。 |
| 缓存/队列 | Redis + Celery | 本地 Docker Compose 已启用 Redis AOF 和 Celery worker；重投依据 PostgreSQL outbox。 |

因此，本仓库实际代码以 Python 为 Holmes AI 后端和事故业务 API，NestJS 只作为订单演示服务及测试动作 owner。当前没有新增 NestJS 事故中心或 BullMQ；本机测试事故 API 使用 Python/FastAPI、PostgreSQL 和 Redis/Celery。Holmes 访问 OpenObserve OSS 时由隔离网络中的策略代理限制路径和查询范围；这不等于 OpenObserve 原生 RBAC 或独立用户权限。

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
- [x] **本机测试持久化事故中心：** PostgreSQL 持久化事故/任务/审批/审计/outbox；本地测试身份与资源 RBAC；重启恢复、幂等及审计查询通过单测和 Compose 验收。数据库 schema/migration 仅应用到获授权的本机测试数据库；生产身份集成仍待规划。
- [x] **内存 RCA 数据模型：** 已验证结论必须带 HTTPS 证据链接，未验证结论明确标为 assumption；原型只记录声明，不自动验证其真实性。
- [ ] **真实 RCA 集成：** Holmes 输出逐项绑定 OpenObserve 日志/Trace、发布或 Runbook 来源；证据不足的结论保留为假设并可人工纠错。
- [x] **本地调查 Skill：** 使用当前支持的 `custom_skill_paths`/`SKILL.md` 提供订单故障只读调查步骤，并通过 Holmes `scan_skill_directory` 验证解析；真实 CLI 调查及证据引用仍待模型凭据和 OpenObserve 查询权限。旧 Catalog 仅留作迁移参考。
- [x] **本机持久复盘：** 事故详情提供影响、根因、恢复措施和行动项表单；approver/admin 可保存草稿或标记已审核，viewer/operator 只读；PostgreSQL 保存审核人/时间并将变更写入事故审计时间线（隔离 API 集成测试通过）。
- [x] **内存审批原型：** Agent 只能提交白名单建议，审批经外部授权回调验证；原型不执行运维命令。
- [ ] **生产受控处置：** 独立执行器须重新校验用户身份、授权、动作白名单、幂等键、取消和审计；高风险生产回滚需有验证失败回退。
- [ ] **完整复盘报告：** 本机已实现持久化和人工审核；需结合真实 Holmes 证据及发布记录完善 RCA，并完成浏览器端手工走查。正式签发流程属于后续产品决策。
- [x] **本机告警/任务/Trace 关联：** 持久事故详情串联 alert fingerprint、task ID 和 Trace ID；发布版本双向查询与真实 CI 接入仍待完成。
- [x] **本机任务查询原型：** 告警响应返回 task ID；同一 token 认证的 `GET /tasks/{task_id}` 返回状态、Trace ID 和结果，内存最多保留 1000 条/1 小时。未覆盖告警 ID、发布检索双向接口，且无持久化。
- [x] **本地关联指纹：** 本地 `alert_id` 指纹可查询所关联 task IDs 与 Trace IDs，Webhook 重复响应复用该指纹；它不是 OpenObserve 原生告警 ID，且无发布关联和持久化。
- [ ] **配置安全：** OpenObserve 使用最低权限的独立服务账户；日志查询限制流、时间、数量和查询耗时；对 SQL 策略做安全复核。

## 4. P2：后续扩展

- [x] 建立 20 个结构化已知根因评测案例并生成可重复 mock 报告；**Holmes live 检索覆盖率、诊断质量和误处置率尚未评测**，不可用样本数量或 mock 结果代替。
- [x] 本机接收器 5 分钟进程内去重：带 Trace 告警按告警名和 Trace ID 做幂等键，无 Trace 告警排除触发时间但保留计数差异（9 项接收器测试通过）。
- [ ] Keep 集成或相似故障聚类；跨多个服务的共同根因分析。
- [ ] 审批后可执行的运维动作模板库及验证失败回退策略。

## 5. 首个演示场景与验收

**场景：** 一个订单接口的新版本造成 HTTP 500。前端 SDK 捕获错误并传递 Trace ID；NestJS 日志和 Trace 入库，OpenObserve 告警；HolmesGPT 通过自定义工具检索同一 Trace、最近发布和历史处理手册；输出附证据的诊断，发起人工审批，完成可回滚处置后验证错误率恢复，生成复盘。

**验收标准：** 既要证明正确找到前后端同一业务请求，也要证明无凭据或越权时无法搜索；调查失败可重试；未批准不得执行生产处置；报告包含真实证据链接与可核对的时间线。

### 外部环境验收门槛

以下项目不能由合成样本或本机 `/bin/echo` 演示替代。接入前需要准备对应环境；完成时保留脱敏后的请求/响应、任务轨迹或测试记录，不把凭据提交到仓库。

| 待验收项 | 开始验收所需条件 | 可验收结果 |
| --- | --- | --- |
| 真实 Holmes 调查及 RCA | 可运行的 Holmes CLI、有效模型凭据、目标 OpenObserve 地址及只读服务账户；账户仅开放调查所需流 | 告警触发真实调查；回答引用可核对的日志/Trace/发布/Skill 证据；没有证据的结论标为假设 |
| OpenObserve 生产权限 | 确定目标部署版本/版本类型，创建独立只读服务账户并配置流级权限与 `allowed_streams` | 允许的流查询成功；未授权流、复杂/越界查询和凭据重定向验证被拒绝 |
| Git/CI 发布关联 | 选定 GitHub、GitLab 或 Jenkins；提供测试仓库/流水线及安全 webhook 凭据的配置方式 | 一次测试发布的版本、commit、变更文件和时间可与告警窗口关联；伪造签名被拒绝 |
| 持久化事故中心 | 用户批准 PostgreSQL schema/migration；提供目标 PostgreSQL 和企业身份/RBAC 集成约束 | 重启恢复、并发幂等、身份授权、审批审计和告警/任务/Trace/发布查询通过验收 |
| 受控处置执行 | 明确可用的测试环境、允许动作清单、独立执行身份、审批人及验证失败回退方案 | 未审批/越权动作拒绝；幂等执行、取消、审计和回退均有可复现记录；生产执行另行授权 |
| 20 例故障评测 | 可注入或回放的隔离环境、模型凭据、OpenObserve 只读访问；评测阈值待项目方确认 | Holmes 对每例实际查询证据并输出诊断；按确认后的指标统计检索覆盖率、诊断质量、误处置率和恢复时间 |
| Keep / 相似故障聚类 | 先确定采用 Keep 还是自建聚类、事故归并字段和可接受的误合并率；若接 Keep，再提供测试实例和凭据 | 同类告警按确定规则归并，并用重复/相似/不可合并样本验证聚类结果；当前内存指纹不等于该能力 |
| 运维动作模板库 | 先确认允许动作、参数约束、审批角色、执行目标和逐动作回退/验证规则 | 模板只生成经过校验的待审批建议；未批准不可执行，失败时按对应验证与回退流程留痕 |
| 演示录像 | 能完整启动的本地演示环境；录制期间使用临时凭据并避免暴露终端秘密 | 录像覆盖故障注入、告警到达及可用的调查结果；当前真实 Holmes 调查未通过前，不将 `/bin/echo` 结果包装为 Agent 诊断 |

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

**最近验证（2026-09-25）：** 订单服务 `npm test`（11 passed）、`npm run build`、告警接收器 pytest（9 passed）、事故流程 pytest（13 passed）、OpenObserve Toolset/Skill 定向 pytest（58 passed，使用 `--no-cov`；不加参数时这组子集触发全仓覆盖率门槛，15.31% 未达标）、Holmes Skill 扫描解析、Compose 配置检查和 `git diff --check` 均通过。真实浏览器点击订单得到 HTTP 500；OpenObserve 的 `app_logs`、`frontend_errors` 和 Trace 均命中同一 Trace ID。Docker 中用 `RELEASE_VERSION=v1.0.1` 注入 HTTP 500，再发送签名发布事件；OpenObserve 查询同时命中错误记录和发布事件，且 release 版本一致；无签名请求返回 401。SQL 告警实际触发本机接收器。真实 Holmes 调查仍未运行，当前回放以 `/bin/echo` 替代 Holmes CLI。

## 2026-09-25 增量：已知根因离线样本

- [x] 新增 `examples/openobserve-aiops/evals/known_root_causes.json`，包含 20 个结构化合成证据案例；每例列明症状、证据、预期结论、不可由现有证据支持的说法及安全后续步骤。
- [x] 新增离线结构校验，保证案例数量、ID 唯一及字段完整（定向 pytest 通过）。
- [ ] 这些样本没有连接真实 OpenObserve，也未由 Holmes/模型进行诊断；检索覆盖率、诊断质量、误处置率和恢复时间仍需故障注入回放及有凭据的真实评测。

**最近验证（2026-09-25）：** `/tmp/poetry185/bin/poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py`（1 passed）、JSON 解析和 `git diff --check` 通过。首次不带 `--no-cov` 的单文件运行触发全仓覆盖率门槛（14.72%，门槛 46%），不是该语料断言失败；定向复跑通过。

## 2026-09-25 增量：统一本地演示服务编排

- [x] 告警接收器从独立容器并入 `holmesgpt-aiops-goal` Compose 项目，与 OpenObserve 和订单服务同组；端口仅绑定 loopback，鉴权 token 由运行环境提供。
- [ ] Compose 接收器仍默认使用 `/bin/echo` 演示调度；真实 Holmes CLI、只读配置与 OpenObserve 访问仍需单独接入和验证。

**最近验证（2026-09-25）：** 告警接收器定向 pytest（9 passed）、Compose 配置解析、接收器镜像构建通过；`docker compose up -d --build` 后同一项目标签下有 `openobserve`、`order-service`、`alert-trigger` 三个容器。订单页、OpenObserve `/healthz`、接收器 `/healthz` 均返回 HTTP 200；签名鉴权的本机演示 Webhook 返回 HTTP 202，任务查询到 `completed`。该任务仍由 `/bin/echo` 替身完成，不代表 Holmes 调查通过。

## 2026-09-25 增量：本机 Docker 测试优先的项目范围

- [x] 用户明确当前先部署到本机 Docker 测试环境，后续再上正式环境；项目 Spec 和路线图已据此限定边界。
- [x] Spec 已通过独立审查并按用户确认的本机 Docker 测试范围进入 Spec 批准状态。
- [x] 用户要求跳过 Plan 审核并直接按已登记任务编码；本机测试 schema/migration 获得明确授权。
- [x] 持久事故/任务、可靠重试、事故工作台、测试身份/RBAC、demo 订单审批处置、验证/回退、20 例可重复 mock 评测及 Compose 恢复文档已实现并完成本机验证。
- [ ] 真实 Holmes 只读证据调查仍阻塞：缺少有效模型凭据；本机 OSS 由查询策略代理和网络隔离约束，API health、mock 报告不能代替 live RCA。
- [x] 正式生产部署和真实生产操作不属于当前授权范围；数据库 schema/migration 仅应用于本机测试数据库。

## 2026-09-25 增量：T000–T009 本机部署与验收状态

- [x] Holmes API、incident API/worker、PostgreSQL、Redis、OpenObserve 和 NestJS order-service 在同一 Compose project 启动；隔离测试 profile 42 passed。
- [x] 本机告警创建持久 incident/task；operator 自审批被拒绝；approver 批准后动作可执行，并已验证切至 ON 后恢复 OFF。
- [x] API、worker、Redis 和 PostgreSQL 重启恢复；pg_dump/pg_restore 在单独数据库成功，原测试数据卷保留。
- [x] 20 例 mock JSON 报告生成且标记 `not_scored`；这不表示 Holmes 诊断准确率通过。
- [ ] 最近真实告警调查因 `holmes_unavailable` 失败关闭；配置模型 API 与 OpenObserve 只读凭据后才能完成 AC-04 和 live 验收。

## 2026-09-25 增量：Holmes 切换 DeepSeek

- [x] 本机 Compose 默认模型切换为 `deepseek/deepseek-flash`，`DEEPSEEK_API_KEY` 仅映射到 Holmes 容器现有的 `MODEL_API_KEY` 配置；模型可被仓库当前 LiteLLM 版本识别并支持 function calling。
- [x] Compose 配置校验和本机 Holmes 镜像重建通过；API liveness 返回 healthy。当前 API Key 未配置，因此这不是一次真实模型调用或 live RCA 验收。
- [ ] 提供有效 DeepSeek API Key 后完成真实模型调查验收。OpenObserve OSS 没有原生 RBAC；本机策略代理仅约束 Holmes 入口，不提供原生用户/租户隔离。

## 2026-09-25 增量：OpenObserve OSS 查询策略代理

- [x] 新增固定上游只读代理，仅放行日志流列表与搜索 API；要求独立客户端 Basic Auth，只返回 `app_logs`、`frontend_errors`，并以 SQLGlot ClickHouse AST 限制为单表 SELECT。
- [x] 代理在服务端限制查询窗口、超时、结果行数、请求体和上游响应大小，并过滤流列表与搜索结果字段；拒绝未知路由、非日志流、非 allowlist 表和复杂查询。
- [x] Compose 网络把 Holmes 与 OpenObserve/telemetry writer 分开；Holmes 仅通过代理网络连接策略代理，order-service 继续通过 telemetry 网络写日志。UI 只映射到宿主机 loopback。
- [x] 11 项代理策略单测通过；Holmes 容器 DNS 不能解析 `openobserve`；Holmes Toolset 经代理看到两条 allowlist 流并成功完成一次本机 OpenObserve 查询。
- [ ] DeepSeek API Key 未设置，真实模型驱动的 Holmes RCA 和事故流程尚未验证。该代理不改变 OpenObserve OSS 缺少原生 RBAC 的事实，也不防护代理/宿主 Docker 管理员被攻破。

## 2026-09-25 增量：事故复盘持久化与工作台

- [x] 新增 `0003_incident_retrospectives` migration，为每个事故持久化影响、根因、恢复措施、行动项、草稿/已审核状态及编辑/审核人和时间。
- [x] 新增复盘读取/保存 API；approver/admin 可保存和审核，viewer/operator 仅能读取，跨资源仍由服务端 scope 检查；复盘操作追加审计事件。
- [x] 事故详情工作台提供复盘表单和审核状态；`DEMO.md` 说明手工流程，并更正 OpenObserve OSS 通过代理隔离而非“只读账户”的描述。
- [x] 隔离 Compose 测试 42 passed；当前本机数据库已应用 migration；本机 HTTP smoke 验证登录、复盘读取、operator 写入拒绝和工作台脚本已通过。
- [ ] 浏览器人工走查仍待完成；真实 Holmes 证据驱动的 RCA/复盘仍待 DeepSeek API Key。

## 2026-09-25 增量：20 例评测语料完整性

- [x] 评测 JSON 加入重复键拒绝校验，防止 JSON parser 静默覆盖同名字段；20 例语料、fixture 到发布/Runbook 的引用测试 **3 passed**。
- [x] 重新生成 20 例 mock 报告并检查每例诊断为空、总评为 `not_scored`；明确 mock 结果不代表 Holmes 诊断能力。
- [ ] Live 20 例需要有效 `DEEPSEEK_API_KEY`；现有 fixture Trace ID 不保证在真实遥测中有匹配，运行结果仍需按实际证据判断。

## 2026-09-25 增量：事故原型和告警输入边界

- [x] 事故内存原型记录 owner、severity、idempotency key、重复告警事件、审批决策、证据/假设分类及恢复时间；证据 URL 会剔除片段并脱敏常见凭据查询参数；复盘可录入影响范围和改进项，字段齐备时进入待复核；13 项流程测试通过。
- [x] Webhook 仅把数值计数和合法 ISO 时间作为摘要交给 Holmes，明确告警名/元数据为不可信数据，不把 CLI stderr 写入任务日志；9 项测试通过。
- [x] 5 分钟去重身份不受 Trace 告警的计数/触发时间变化影响；无 Trace 告警仅忽略触发时间，计数仍影响身份。
- [x] 通过认证的进程内任务查询可按 task ID 查看调查状态与 Trace ID；完成结果有数量/时长上限，9 项测试通过。
- [x] 通过认证的 `/alerts/{alert_id}` 可从本地告警指纹反查任务与 Trace；重复 Webhook 复用同一指纹。
- [x] 发布事件入口校验原始请求体 HMAC-SHA256，拒绝缺少/错误签名、未知字段不入库、限制文件数和字段长度；实际演示事件按 release/commit 在 OpenObserve 命中。
- [x] 在 loopback 绑定的本机接收器上实测 Docker 网络 Webhook 返回 HTTP 202，`/bin/echo` 完成调度并保留 Trace ID。
- [x] 修复 `GET /monitoring.js` 静态 SDK 路由；真实浏览器产生 HTTP 500 后，本机 OpenObserve 三种数据均以同一 Trace ID 命中。
- [ ] 上述状态仍为内存原型，未具备服务端身份/RBAC、持久任务存储、进程重启恢复和生产执行器。
