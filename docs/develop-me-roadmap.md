# AI 智能运维与故障诊断平台：二次开发路线图

> 仓库：`monstereat/holmesgpt`｜固定开发分支：`develop-me`｜更新：2026-09-28
> 开源底座：HolmesGPT；集成 OpenObserve、前端监控 SDK、NestJS、OpenTelemetry，Keep 按需加入。  
> 范围：先在当前电脑 Docker 测试环境围绕真实应用故障建立「采集 → 告警 → 调查 → 审批处置 → 验证 → 复盘」闭环；正式环境部署后续另行规划和授权；不自建日志数据库。

> **当前验收状态（2026-09-28）：** 本机 incident API 的事故分级、负责人指派和状态流转仍由服务端逐次校验 operator/admin 角色、order-service 资源范围及合法状态转换，并记录审计；closed 为终态，resolved/closed 不可分级或指派。本机持久 PostgreSQL 和 migration image 已更新至 `0012_break_glass_admin_recovery`。API 使用 `aiops_runtime`、Celery worker 使用最小权限 `aiops_worker`、migration 使用 `aiops_migrator`；break-glass role 仅可执行专用恢复函数，API runtime DELETE 权限仅限 OIDC 临时登录和撤销会话表。隔离 PostgreSQL role/backup verifier 覆盖 12 个迁移，3 项 CLI 单元测试通过；本机 Compose role verifier 与 9 项服务 readiness 检查通过。迁移后 incidents/tasks/audit/outbox 行数保持 **12/12/54/12**。Incident API 和 Holmes readiness 返回成功，worker healthy；DeepSeek key 由本机 mode-0600 私密文件提供。worker 权限隔离后，一条 synthetic webhook 调查首试完成，结果含该事故 Trace ID 和 OpenObserve 搜索工具调用，并记录 `task.completed`。这只验证本机测试闭环；历史 20 案报告诊断仍 `not_scored`，不代表准确率或生产效果。PostgreSQL 与 Redis TLS verifiers 均在本机及 GitHub Actions [run 36378776730](https://github.com/monstereat/holmesgpt/actions/runs/36378776730) 通过，但不覆盖真实托管服务。没有部署到生产环境。

> **数据库上线差距：** 本机 PostgreSQL 16 的 migrations `0001`–`0012`、API/worker/migrator/break-glass 分权和隔离备份恢复均已验证。数据库发布顺序、迁移失败处理、应用回滚兼容边界和恢复步骤见 [`POSTGRESQL-OPERATIONS.md`](../examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md)。目标托管数据库尚未选定，因此 provider/IAM 兼容、加密异地备份、PITR、实测恢复时间及 staging 回滚演练未完成。

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
- [x] 告警 worker 到本机 Holmes 非流式 API 的调查链路和 DeepSeek live 调用、20 案评测曾完成；当前本机 DeepSeek key 缺失，无法运行态复验。具体历史检索覆盖见留存报告，根因诊断质量仍待人工 rubric 评分。
- [x] 建立可回放的订单 HTTP 500 故障注入；调查录像是可选演示材料，不作为产品验收项。

## 3. P1：展示工程可靠性的功能

- [ ] **外部 Git/CI 发布源（已具备发送器、GitHub 可复用工作流和本机端到端验收）：** `publish-release-event.mjs` 通过 HTTPS 对标准化元数据使用 HMAC-SHA256 签名；`.github/workflows/publish-aiops-release-event.yml` 可由部署后 job 调用，并只声明 URL/签名密钥为 secret。单测覆盖签名、字段限制、HTTP/redirect 拒绝和失败响应；`verify-release-event-e2e.sh` 已验证本机 sender→NestJS receiver→OpenObserve 持久化。实际部署 workflow 调用、受保护远端 secret binding 和 staging 发布事件仍未完成；GitLab/Jenkins 尚无 adapter。
- [x] **本地发布事件原型：** NestJS 接受标准化发布事件，要求 HMAC-SHA256 签名并将版本/commit/变更文件写入 `app_logs`；调查提示要求按告警时间检索。Docker → OpenObserve 查询端到端已验；`bash examples/openobserve-aiops/verify-release-event-e2e.sh` 使用临时密钥与容器复核签名接收和准确事件持久化。
- [x] **事故流程内存原型：** `examples/openobserve-aiops/incident_workflow.py` 实现状态转换、负责人、严重级别、幂等键、重复告警归并、审批事件和时间线；不执行处置命令。
- [x] **本机测试持久化事故中心：** PostgreSQL 持久化事故/任务/审批/审计/outbox，以及严重级别和负责人；migration `0008_incident_triage` 已应用于获授权的本机测试数据库。operator/admin 才能管理，候选负责人必须是启用且有 order-service 范围的 operator/admin；状态流转由 API 按显式矩阵校验，并追加审计，closed 为终态。resolved/closed 事故冻结分级/指派。测试和本机 API/UI 验收通过；生产身份集成仍待规划。
- [x] **内存 RCA 数据模型：** 已验证结论必须带 HTTPS 证据链接，未验证结论明确标为 assumption；原型只记录声明，不自动验证其真实性。
- [ ] **真实 RCA 结果验收：** Holmes 调查 API、只读证据代理和带来源/查询信息的证据保存已实现，DeepSeek live 模型调用已实测；最新报告 `9d27bceb636747d192b839868afae3c9` 有 20/20 当前 run/case 证据查询命中、3/3 发布事件命中、0 个未限定成功搜索、0 个跨案命中、0 个工具错误。真实模型输出的根因准确性仍待两名独立评审按 rubric 评分并裁决，`not_scored` 不能等同于准确率通过。
- [x] **本地调查 Skill：** 使用当前支持的 `custom_skill_paths`/`SKILL.md` 提供订单故障只读调查步骤，并通过 Holmes `scan_skill_directory` 验证解析；真实 CLI 调查及证据引用仍待模型凭据和 OpenObserve 查询权限。旧 Catalog 仅留作迁移参考。
- [x] **本机持久复盘：** 事故详情提供影响、根因、恢复措施和行动项表单；approver/admin 可保存草稿或标记已审核，viewer/operator 只读；PostgreSQL 保存审核人/时间并将变更写入事故审计时间线（隔离 API 集成测试通过）。
- [x] **内存审批原型：** Agent 只能提交白名单建议，审批经外部授权回调验证；原型不执行运维命令。
- [ ] **生产受控处置（当前范围之外）：** 本机只实现固定的 order-service 测试动作、独立审批、owner 侧授权复验、幂等、执行后验证、失败恢复和审计；生产执行器/真实生产权限必须另行设计和授权。
- [ ] **完整复盘内容验收：** 本机已实现持久化、角色权限和人工审核；Holmes 真实诊断接入后需再核对 RCA/发布证据，并完成浏览器登录后的手工走查。正式签发流程属于后续产品决策。
- [x] **本机告警/任务/Trace 关联：** 持久事故详情串联 alert fingerprint、task ID 和 Trace ID；发布版本双向查询与真实 CI 接入仍待完成。
- [x] **持久任务查询与关联：** incident 详情返回其 PostgreSQL 持久 task 状态、Trace、结果和审计；重复告警由稳定指纹幂等归并，outbox/worker 支持重投、租约回收和有界重试。它不是 OpenObserve 原生告警 ID。
- [x] **本机只读查询边界：** OpenObserve OSS root 凭据只交给本机策略代理，Holmes 只能经隔离网络访问 allowlist 流列表与受限搜索；查询窗口、耗时、行数和响应均受限，SQL 用 ClickHouse AST 检查。该机制不是 OpenObserve 原生 RBAC，正式环境需评估支持服务端 RBAC 的版本。

## 4. P2：后续扩展

- [x] 建立 20 个结构化已知根因案例、本机 synthetic evidence seeder 和 mock/live 报告生成器；缺 key 的 live 安全门及 OpenObserve→Holmes 只读检索路径已测。真实 live 检索覆盖已按例记录；**根因诊断质量和误处置率尚未人工评分**，mock 结果不代表诊断能力。
- [x] 本机接收器 5 分钟进程内去重：带 Trace 告警按告警名和 Trace ID 做幂等键，无 Trace 告警排除触发时间但保留计数差异（9 项接收器测试通过）。
- [ ] Keep 集成或相似故障聚类；跨多个服务的共同根因分析。
- [x] 本机单一 `set-chaos-mode` 测试动作经审批后执行，执行后状态核对、失败恢复和审计已验；通用运维动作模板库不在当前本机测试目标内。

## 5. 首个演示场景与验收

**场景：** 一个订单接口的新版本造成 HTTP 500。前端 SDK 捕获错误并传递 Trace ID；NestJS 日志和 Trace 入库，OpenObserve 告警；HolmesGPT 通过自定义工具检索同一 Trace、最近发布和历史处理手册；输出附证据的诊断，发起人工审批，完成可回滚处置后验证错误率恢复，生成复盘。

**验收标准：** 既要证明正确找到前后端同一业务请求，也要证明无凭据或越权时无法搜索；调查失败可重试；未批准不得执行生产处置；报告包含真实证据链接与可核对的时间线。

### 外部环境验收门槛

以下项目不能由合成样本或本机 `/bin/echo` 演示替代。接入前需要准备对应环境；完成时保留脱敏后的请求/响应、任务轨迹或测试记录，不把凭据提交到仓库。

| 待验收项 | 开始验收所需条件 | 可验收结果 |
| --- | --- | --- |
| 真实 Holmes 调查及 RCA | 可运行的 Holmes CLI、有效模型凭据、目标 OpenObserve 地址及只读服务账户；账户仅开放调查所需流 | 告警触发真实调查；回答引用可核对的日志/Trace/发布/Skill 证据；没有证据的结论标为假设 |
| OpenObserve 生产权限 | 确定目标部署版本/版本类型，创建独立只读服务账户并配置流级权限与 `allowed_streams` | 允许的流查询成功；未授权流、复杂/越界查询和凭据重定向验证被拒绝 |
| 生产遥测采集链路 | 选定平台的私网入口、TLS 证书/信任链、secret-file 挂载方式、OpenObserve 写入身份和加密持久队列存储；确认 OTel File Storage beta 组件可接受 | 无 token/TLS 的 OTLP 请求被拒绝；合法请求经 HTTPS 写入；队列限额、重试、重启续传和 secret rotation 按目标平台实测 |
| Git/CI 发布关联 | 选定 GitHub、GitLab 或 Jenkins；提供测试仓库/流水线及安全 webhook 凭据的配置方式 | 一次测试发布的版本、commit、变更文件和时间可与告警窗口关联；伪造签名被拒绝 |
| 持久化事故中心 | **本机测试版已完成**；生产接入仍需目标 PostgreSQL 和企业身份/RBAC 集成约束 | 本机重启恢复、并发幂等、身份授权、审批审计和告警/任务/Trace 查询已验收；发布查询和生产集成仍待完成 |
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

**状态说明：** [x] 只表示该条目的描述范围已在本机测试环境实现并有对应验证，不代表生产接入。后续章节记录更具体的实现边界、实测证据和未完成事项。当前 Holmes 查询由 AST 约束、allowlist 和网络隔离共同限定；OpenObserve OSS 本身仍无原生 RBAC。

### 历史增量说明

> 下方带日期的增量记录各次改动完成时的状态，保留当时的 `[ ]`/`[x]` 作为历史快照，不应单独当作当前待办清单。当前项目进度以根目录 [`ROADMAP.md`](../ROADMAP.md) 为准；生产环境输入和验收缺口以 [`PRODUCTION-READINESS.md`](../examples/openobserve-aiops/PRODUCTION-READINESS.md) 为准，简历可声称范围见 [`RESUME-CLAIMS.md`](../examples/openobserve-aiops/RESUME-CLAIMS.md)。

## 2026-09-24 增量：OpenObserve 严格范围及凭据保护

- [x] 新增 `allowed_streams` 配置；配置后只允许单一白名单流的简单 SELECT 查询，拒绝 JOIN、UNION、子查询、CTE 和未授权流。
- [x] Trace 查询工具和日志流发现同样尊重白名单；日志流列表最多返回 100 条。
- [x] 上游 HTTP 错误消息脱敏；Basic Auth 请求不跟随重定向。
- [x] `tests/plugins/toolsets/openobserve/test_stream_scope.py` 覆盖越权流、复杂 SQL、Trace 越权和上游敏感错误响应。
- [x] `holmes/plugins/toolsets/openobserve/SECURITY.md` 记录生产最小权限要求；最近的专用 GitHub Actions 检查已通过。
- [ ] **生产接入仍待完成：** 服务端只读账户和流级 RBAC、真实 OpenObserve 集成、真实告警触发/审批/执行器；未配置白名单时保留开发模式，禁止将其当作生产安全保证。

## 2026-09-25 增量：本地 OpenObserve AIOps 演示

- [x] 新增 Docker Compose 本地 OpenObserve + NestJS 订单服务、故障注入、前端遥测 SDK 和本机告警接收器示例。
- [x] 增加签名发布事件入口、发布元数据白名单/长度限制、通用 CI 发送脚本和可复用 GitHub Actions adapter，并让调查提示按告警时间检索版本、commit 和变更文件；真实部署 caller、secret binding 与端到端 CI 事件仍待接入。
- [x] 修正 OTLP HTTP Trace 接收路径与 stream-name；固定 OpenObserve 镜像版本，增加服务启动等待、Webhook 本地 SSRF 限制说明。
- [x] SDK 脱敏/采样、告警 JSON 解析、鉴权、并发上限与重复告警过滤均有本地测试。
- [x] 本地容器验证 OpenObserve health、订单服务启动、HTTP 500 故障注入及日志/前端错误/Trace 的 Trace ID 关联。
- [x] 本地 UI 配置订单错误趋势仪表盘、告警模板、Webhook 目标和 SQL 告警；实际评估成功触发，接收器收到 `trace_id` 并异步启动调查命令。
- [x] 事故内存原型补充负责人、严重级别、幂等去重、证据/假设分类、恢复时间、URL 凭据参数脱敏和不臆造信息的复盘草稿；`test_incident_workflow.py` 12 项通过。
- [ ] 当前触发验证用 `/bin/echo` 替代 Holmes CLI；真实 Holmes 安装、OpenObserve 只读凭据与真实调查结果未验证。
- [ ] 调查录像仍未完成；**本机持久化事故状态、审批和固定演示处置已在后续增量完成**；生产审批和生产处置执行器仍未完成。

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
- [ ] 浏览器已确认登录页可加载且无 console error；受本机测试账号凭据未进入浏览器自动化，本轮没有登录后手工走查审批、执行和复盘流程。后端角色/API 已由隔离测试覆盖；真实 Holmes 证据驱动的 RCA/复盘仍待 DeepSeek API Key。

## 2026-09-25 增量：20 例评测语料完整性

- [x] 评测 JSON 加入重复键拒绝校验，防止 JSON parser 静默覆盖同名字段；20 例语料、fixture 到发布/Runbook 的引用测试 **3 passed**。
- [x] 重新生成 20 例 mock 报告并检查每例诊断为空、总评为 `not_scored`；明确 mock 结果不代表 Holmes 诊断能力。
- [x] Live runner 已在每次评测时为病例生成唯一 Trace ID，并把本轮合成证据写入本机 OpenObserve；不依赖已有 fixture Trace ID。
- [ ] Live 20 例需要有效 `DEEPSEEK_API_KEY`；模型结论、工具调用和诊断结果仍需按实际证据判断。

## 2026-09-25 增量：Live 评测本机证据接入

- [x] Live runner 发送数据前检查 DeepSeek key、Holmes 本机 URL 和健康状态；缺 Key 时安全退出，不写入评测数据。
- [x] Live runner 为 20 个合成病例生成唯一 Trace ID，并写入本机 OpenObserve `app_logs`；报告记录评测 run ID、每例 Trace ID 和准确写入数量。每轮最多追加 100 行，不删除既有数据；live 仍需显式 `--confirm-live`。
- [x] 本机 OpenObserve 实际接受 40 条记录（20 例，每例 2 条）；Holmes 经只读策略代理按首例 Trace ID 查询到两条匹配的 `synthetic_fixture` 记录。此项证明本机证据链可用，不代表模型诊断通过。
- [x] Seeder URL / JSON ingestion / 部分写入失败测试新增；评测、seed、安全边界和 Holmes API 契约定向测试 **38 passed**；最新 Compose 隔离测试 **46 passed, 1 warning**。
- [x] 三个带发布记录的用例现在以 `release_deployed` 结构化事件写入，且只包含语料中明确给出的 release/changed files；未给出的 commit SHA 存为空。对应 Runbook 原文与仓库路径会放入该例 Holmes 只读调查上下文；有配置关联 Skill 的病例同时提供 Skill 来源。
- [x] Holmes 自定义技能目录纳入库存故障、数据库 schema/migration 不匹配、发布回归三个只读 Skill；运行中的 Holmes 容器从实际挂载路径扫描到 3 个 Skill，skill loader 定向测试通过。
- [x] 报告 schema 1.1 增加逐例 exact run/case 证据匹配与 release event 匹配状态，并汇总 live 检索覆盖计数；mock 报告经 Draft 2020-12 schema validator 验证。诊断准确度不伪造分数，仍为 `not_scored`。
- [ ] DeepSeek 模型调用、工具调用、根因诊断质量和 20 例 live 评测仍待本机有效 `DEEPSEEK_API_KEY`。缺 Key 的拒绝路径已验证，并在写入 OpenObserve 之前退出。

**最近验证（2026-09-25）：** `python3 examples/openobserve-aiops/evals/run_evals.py --mode mock` 生成 20 条 `not_scored` 报告且通过 JSON Schema 1.1 校验；live 缺 Key 检查按预期拒绝且未 seed；定向 pytest（38 passed）；隔离 Compose profile（46 passed, 1 warning）。结构化发布事件/Runbook 上下文有单测覆盖，Holmes 容器扫描到 3 个项目 Skill，未在当前本机 OpenObserve 重复追加数据。

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

## 2026-09-25 增量：评测报告参考答案与模型输出分离

- [x] 报告 schema 1.2 将病例参考预期、参考不支持结论和参考安全建议命名为 `reference_*`，避免与 Holmes 的自由文本回答混淆；未解析的假设使用 `null`，不把空数组解释为模型“没有假设”。
- [x] 根因评分仍保持 `not_scored`，直到采用可辩护的评审规则；mock 结果不声称模型准确率。
- [x] 评测报告定向测试 **11 passed**；重生成的 20 例 mock 报告通过 Draft 2020-12 JSON Schema 校验。
- [ ] DeepSeek live 调查和 20 例 live 评测仍需本机配置有效 `DEEPSEEK_API_KEY` 并显式确认外部模型调用。

## 2026-09-25 增量：超大工具结果错误摘要长度

- [x] 缩短超大工具结果落盘后的错误摘要模板，避免长临时路径加较长命令示例挤占预览预算；定向工具上下文限制测试 **11 passed**。
- [x] 全仓非 LLM 回归重跑为 **3858 passed、160 skipped、2 failed**，比前次少一项失败。
- [ ] 全仓仍有 SSRF 测试 HTTP 502 与交互渲染测试未显示 `(error)` 两项失败，不能记作全仓通过。

## 2026-09-25 增量：窄栏交互错误标记与全仓回归

- [x] 双栏 TUI 将 `(error)` 标记移至工具标签之前，窄栏宽度下不会被右侧裁剪；`tests/test_interactive.py` **67 passed**。
- [x] 全仓非 LLM 回归 **3859 passed、160 skipped、1 failed**；此前记录的交互错误标记失败已消失。
- [ ] 唯一剩余全仓失败是 `test_pinned_adapter_connects_to_validated_ip` 请求返回 HTTP 502。

## 2026-09-25 增量：代理环境下的 SSRF IP pinning

- [x] 实测 `requests.Session` 从环境读取代理后，连接到了本机代理端口而非 URL 校验所得的 loopback 测试服务，绕过 pinned adapter。`fetch_webpage` 禁止继承环境代理和 `.netrc`，继续通过 IP-pinned adapter 直连；仍支持 `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE`。
- [x] SSRF 测试增加代理环境覆盖；`tests/plugins/toolsets/test_internet_ssrf.py` **41 passed**。
- [x] 全仓非 LLM 回归最终结果：**3861 passed、160 skipped、0 failed、119 warnings**。

## 2026-09-25 增量：20 案隔离与评测轨迹审计

- [x] 每个合成案例使用独立事件时间，案例间距 62 分钟，大于 Holmes 只读代理的一小时查询窗；Seeder 按 run/case/time window 逐案确认记录数和可见性，避免同一轮案例互相污染。
- [x] 本机测试 Compose 的 OpenObserve `ZO_INGEST_ALLOWED_UPTO` 设为 24 小时，容纳最长约 20 小时的测试样本跨度。此设置仅用于本机测试；官方默认是五小时，生产写入策略需单独评审。
- [x] 报告 schema 1.3 增加当前 run 的可识别跨案例 fixture 命中和未精确过滤 run/case 的成功搜索计数。隔离 live 报告验证为 20/20 精确案例证据命中、3/3 发布事件命中、0 个可识别外案 fixture 命中、9 个未精确过滤的成功搜索；报告通过 Draft 2020-12 校验。
- [x] 独立 AI 复核前后 20 案，发现诊断文字陈述了报告未保存的失败查询；这不是人工诊断评分，`scoring` 继续保持 `not_scored`。
- [x] Holmes 客户端在同时存在成功结果时保留 allowlist 工具的脱敏失败状态和参数，避免只保存成功结果而无法复核部分模型陈述；无成功证据仍失败关闭。
- [x] 隔离 Compose 测试 **70 passed, 1 warning**；Seeder 测试 **18 passed**；schema 1.3 校验通过。
- [ ] 重新运行 live 评测以验证新失败轨迹留痕，然后由人工按 rubric 评分；完成前不宣称诊断准确率。

## 2026-09-25 增量：事故队列指标端点

- [x] Incident API 新增 token 保护的 Prometheus text endpoint `/_internal/metrics`，输出 queued/running/retrying/completed/failed/cancelled 任务数、最老 pending age 和持久 retry attempts，不暴露用户或事故 label。
- [x] 指标默认关闭；非 local 模式启动会拒绝缺失或短于 32 字节的 scraper token。生产网络必须限制为私有 Prometheus/scraper，Compose 未额外发布端口。
- [x] 隔离 Compose 回归 **70 passed, 1 warning**；在运行中的本机 PostgreSQL 验证指标数据并确认 readiness HTTP 200，随后移除测试 token，指标端点回到默认 404。
- [ ] 后续增量已实现 pending-task-age/capacity/failed-task/API scrape Prometheus-compatible alerts，并要求生产配置 owner-selected `AIOPS_OLDEST_PENDING_TASK_AGE_SLO_SECONDS`；production 仍需私网抓取、通知路由与经容量测试确定的容量 SLO。worker task latency、Holmes model-call latency 与 LiteLLM 正值成本估算指标已在后续增量中实现并于本机验证。

## 2026-09-25 增量：生产迁移凭据边界与回归修复

- [x] 非 local 环境运行 `migrate.py` 必须使用独立 `MIGRATION_DATABASE_URL`；不再回退到应用运行身份的 `DATABASE_URL`。本机 Compose 显式标记为 local 并沿用本地测试身份，生产部署仍须为迁移 Job 配置 schema 级权限身份。
- [x] 修复 live evaluation release event matcher 的未定义 `normalized` 变量；修复 Holmes 只有失败工具调用时被误接受的问题，同时保留“有成功证据时附带记录失败调用”的行为。
- [x] 隔离 Compose incident suite **78 passed, 1 warning**。
- [ ] 生产平台的 migration Job、数据库角色/授权脚本及前向/回滚演练仍需按目标 PostgreSQL 平台生成和验收。

## 2026-09-26 增量：持久队列容量与 webhook 背压

- [x] 生产模式要求设置正整数 `AIOPS_MAX_PENDING_TASKS`；本机 local 模式保持可选，不为生产容量填猜测默认值。
- [x] Incident API 在 PostgreSQL transaction advisory lock 下检查 queued/running/retrying 总数，跨 API 副本原子拒绝超限新告警；相同 fingerprint 先返回已有 incident，队列满也保持幂等。
- [x] 队列满返回 503 和 `Retry-After: 30`；监控端点新增 pending capacity gauge，oldest-pending age 包括 running 任务。
- [x] 配置、store admission、重复告警和 webhook 503 测试通过；真实 PostgreSQL admission 回归现以 barrier 同步 **16 个并发事务** 对冲 capacity 4，并核对准确准入数及重复告警幂等。该用例单独运行时会先在隔离测试库应用 migrations；定向 Docker 测试 **1 passed (10.82s)**。这验证并发正确性，不是吞吐压测或生产 cap 的依据。
- [ ] 生产 cap 值须由目标环境的负载/worker 吞吐压测确定；平台专用 admission 告警和实际 OpenObserve retry 行为须在 staging 验收。

## 2026-09-26 增量：评测查询范围服务端强制

- [x] Holmes 客户端将评测 run/case 与 alert trace IDs 作为服务端上下文头传入；OpenObserve 搜索必须包含精确 run/case 条件并通过 AND 连接，拒绝 OR；trace 查询必须属于告警提供的 trace ID 列表。普通生产调查不启用评测 run/case 限制。
- [x] 修复 HTTP 头名小写化后的查找，以及 SQL 字符串字面量内 `-limit-` 被误识别为 LIMIT 子句的问题；定向 OpenObserve 测试 **13 passed**，事故服务 Compose suite **80 passed, 1 warning**。
- [x] 最终 DeepSeek live 报告 schema 1.3 通过校验：20/20 案例证据、3/3 发布事件、0 次未限定成功搜索、0 跨案 fixture、0 工具错误；RCA scoring 仍为 `not_scored`。报告 `/tmp/holmes-aiops-live-report-final.json`，run `18add25121ba4ccdb0955c783b7dafc8`。
- [ ] 生产平台/OIDC Provider 与租户模型、托管数据服务及正式部署边界仍待用户确认；诊断准确率 rubric 与生产 staging 验收未完成。

## 2026-09-26 增量：失败调查留痕与真实 trace 证据校准

- [x] 对照 Holmes 仓库的 OpenObserve tool implementation 确认 `openobserve_find_trace` 将内部 SQL 返回在 `result.params.sql`，不包含原始 `stream` 参数；证据提取器现从 SQL 校验 allowlist stream，并要求 trace ID 与告警输入完全匹配。
- [x] 对仅有失败工具调用的调查，在稳定错误码失败关闭的同时保留已脱敏参数/状态；失败任务持久化该证据，重新 claim/手动重试清除旧 attempt 结果；live eval 报告也写入失败调用证据。
- [x] 跨案例 fixture 审计现在同时查看 `openobserve_find_trace` 和 `openobserve_search_logs` 命中。
- [x] 隔离 Compose incident suite **80 passed, 1 warning**，包括失败 worker 持久化脱敏轨迹及手动重试清空旧结果的验证。最新 DeepSeek 20 案 live 报告 schema 1.3 通过：20/20 当前 run/case 证据匹配、3/3 release 匹配、0 跨案 fixture、0 病例级错误，5 条失败工具轨迹留痕。
- [x] 历史 run 曾出现 15 次成功日志搜索未带完整精确 run+case 条件；当前服务端 query policy 已强制范围，最新 2026-09-27 run `9d27bceb636747d192b839868afae3c9` 的未限定成功搜索为 0。
- [ ] 诊断人工 rubric 仍未完成，`scoring=not_scored`；需两名独立评审完成评分并裁决分歧。
- 报告 `/tmp/holmes-aiops-live-report-trace-audit-v1.3.json`，run ID `52de154d9c3747e59fa5035b1f98bc6f`。

## 2026-09-25 增量：浏览器工作台角色与处置闭环验收

- [x] 本机浏览器分别登录 operator 和 approver；operator 可发起测试动作但不能审批，approver 可审批但不显示执行按钮，operator 在批准后执行。
- [x] 对新建的本机合成事故实际走通 `set-chaos-mode:on` 独立审批和执行；order-service owner 验证 OFF→ON，再独立审批并执行 OFF，验证 ON→OFF。
- [x] operator 页面不显示复盘编辑/审核按钮；approver 保存并审核复盘，工作台显示审核人和状态，审计时间线写入 `retrospective.reviewed`。复盘明确记录没有 DeepSeek Key、没有真实 RCA，不把合成验收写成业务根因。
- [x] 工作台展示合成告警的三次调查尝试与 `holmes_unavailable` 终态；完整真实 Holmes 调查和 live 评测仍未完成。

## 2026-09-25 增量：正式环境准备与验收方案

- [x] 新增 [`examples/openobserve-aiops/PRODUCTION-READINESS.md`](../examples/openobserve-aiops/PRODUCTION-READINESS.md)，根据当前服务职责整理后续部署拓扑、必需配置输入、安全门槛、分阶段 staging 验收和 go/no-go 证据。
- [x] 文档明确当前没有可执行生产 manifest；生产平台、身份提供方、域名/TLS、托管数据服务、OpenObserve RBAC、SLO/RPO/RTO 和动作 owner 均需负责人确认后再生成平台专属配置。
- [x] 未配置生产凭据、迁移、部署或动作；生产上线仍需单独明确授权。

## 2026-09-25 增量：20 案 live 证据索引等待与复测

- [x] OpenObserve ingest 返回全量接受后，live seeder 按唯一 run ID 轮询 `_search`，确认所有记录已可搜索后才开始 Holmes 调查；等待最多 30 秒，超时或部分数据可见时提前失败。
- [x] Seeder 定向测试 **16 passed**；本机真实 OpenObserve 两条记录 smoke test 检索到 2/2；20 案 live 报告通过 Draft 2020-12 schema 校验。
- [x] 当时生成的 live 报告：20 案、40 条 synthetic 行、16/20 本轮病例证据匹配、1/3 release event 匹配、1 个 Holmes 工具错误。该报告后来发现计量器漏计了 SQL 投影未返回过滤列的结果，数字已被后续复核取代；`scoring` 为 `not_scored`，这不是诊断准确率结果。
- [ ] 在把 live evaluation 用作发布门槛前，需人工建立根因诊断 rubric 并记录独立评分。
- [x] 无效尝试（Holmes 容器未加载私有 DeepSeek Key，20/20 请求失败）已与有效 live 报告隔离，不纳入评测指标。
- 结果文件：`/tmp/holmes-aiops-live-report-visibility-valid.json`；实现提交：`02a430bc6`。

## 2026-09-25 复核：修正 live 检索覆盖计量

- [x] 计量器现可识别精确 run/case SQL 范围内的非空查询结果，即使 SELECT 投影未返回过滤 ID 列；时间窗必须覆盖 seed 时间，release 匹配必须在相同 run/case 范围内。
- [x] Seeder/client/evaluation 定向测试 **41 passed**；复核报告通过 Draft 2020-12 schema 校验：20/20 当前 run/case 证据查询、3/3 发布事件查询、0 工具错误。诊断评分仍为 `not_scored`。
- [ ] 根因诊断质量仍需人工评分；生产平台、身份、网络、容量和回滚验收未完成。
- 复核报告：`/tmp/holmes-aiops-live-report-scoped-run-reviewed.json`；原始 live 输出：`/tmp/holmes-aiops-live-report-scoped-run.json`。
