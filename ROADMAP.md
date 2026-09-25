# HolmesGPT AIOps 项目路线图

## 当前阶段

**本机 Docker 测试环境优先：T000–T007 的业务能力已部署，T008 查询代理和 T009 持久复盘已实现；整体验收仍不完整。** Holmes 默认使用 `deepseek/deepseek-flash`。新增策略代理和隔离 Docker 网络后，Holmes 无法直连 OpenObserve，只能通过受限的流列表/搜索端点查询 allowlist 流；本机实测查到数据。DeepSeek API Key 尚未配置，因此真实模型调查待验。OpenObserve OSS 自身仍不提供原生 RBAC；代理只收窄 Holmes 的访问路径。用户授权仅覆盖本机测试 schema/migration 和测试部署，不包含正式环境。

## 已完成并验证

- OpenObserve Toolset 支持有界只读查询、Trace 检索和流范围约束。
- 本地 Compose 编排 OpenObserve、订单服务和告警接收器；浏览器错误、NestJS 日志与 Trace 可用 Trace ID 关联。
- 告警鉴权、进程内去重/任务查询、签名发布事件原型及事故流程内存模型已有定向测试。
- 20 个合成根因证据样本及结构校验已提交；它们尚未用于 Holmes 诊断质量评测。
- T000：order-service 新增仅限 `set-chaos-mode` 的内部测试动作接口；服务端验证 token、资源、参数和幂等键；4 项定向 Node 测试通过，TypeScript build 通过。

## 实施状态与验证

- `specs/001-resume-aiops/` 的 Spec 已获用户确认并通过独立审查；Plan/Tasks 已按用户要求直接批准。数据库迁移获授权仅作用于本机 Docker 测试库。
- T001：事故/任务/审批/审计/outbox PostgreSQL schema、稳定指纹与事务写入、带资源范围的测试角色授权、口令哈希与签名会话已完成；一次性本机 PostgreSQL 集成测试验证迁移、重复事件去重及 outbox 失败回滚。
- T002：Webhook 认证和边界校验后事务创建事故/任务/outbox；Celery worker 有原子 claim、租约、退避重试和失败终态，dispatcher 周期重投队列中丢失的任务并回收过期租约；`trigger.py` 不再调用 Holmes CLI 或 `/bin/echo`。隔离 PostgreSQL 全套测试 21 passed；无数据库时 3 passed、4 个数据库集成项安全跳过。Compose 数据卷和 Redis 联调待 T007。
- T003：Holmes 非流式 `/api/chat` 客户端、状态码与工具结果校验、限定流范围的证据映射和敏感值脱敏已完成；14 项契约测试通过。该结果来自模拟响应，未代表 live Holmes/OpenObserve 验收。
- T004：工作台提供本地登录、服务端角色/资源校验、事故/任务/Trace/证据/审批/审计详情和受限任务重试。一次性本机 PostgreSQL 集成测试 2 passed。
- T005：工作台可申请/批准/拒绝/取消及执行测试动作；operator 与 approver 分权，执行前复验审批和固定白名单，调用 order-service owner 接口并核对实际状态，失败时恢复原状态并审计。一次性本机 PostgreSQL 集成测试 4 passed；`node --check` 和 Compose 配置校验通过。
- T006：评测 runner 可生成 20 例 mock/live 结构化报告；mock 不生成模型诊断且标记 `not_scored`，live 请求需显式 `--confirm-live`，病例来源与实时 OpenObserve 证据分别标记。三条 synthetic 发布上下文准确引用病例证据并关联 Runbook。离线报告已重新生成 20 例，保持 `not_scored`；语料和上下文测试 3 passed，JSON loader 拒绝重复字段。当前缺模型 key，尚未发送 Holmes 模型请求。
- T007：Holmes、事故 API/worker、PostgreSQL、Redis、OpenObserve 和 order-service 已在一个 Compose project 运行；隔离测试基线 42 项通过，最新全套 Compose profile 46 项通过。已实测告警→持久化任务、权限分离审批、demo 动作执行/回滚、API/worker/Redis/PostgreSQL 重启恢复及独立数据库备份/恢复。Holmes 默认已改为 `deepseek/deepseek-flash`，LiteLLM 识别该模型且支持工具调用，Compose 重建及配置校验通过；尚未提供 API Key，真实告警仍以 `holmes_unavailable` 安全失败。OpenObserve OSS 无服务端 RBAC，AC-04/live 整体验收未通过。
- T008：加入固定上游、独立 Basic Auth、仅 streams/search 两条路由、SQLGlot ClickHouse AST、流 allowlist、单小时查询窗、超时/行数/请求和响应大小上限；Holmes 未加入 OpenObserve 或 telemetry 网络。11 项代理策略单测通过。Holmes 容器无法解析 OpenObserve 服务名，通过代理可见 2 条 allowlist 流并实际查询到日志。本机 OpenObserve UI 仍只绑定 loopback。
- T009：新增本机迁移 `0003_incident_retrospectives` 和按事故持久化的复盘 API/工作台表单；approver/admin 可保存影响、根因、恢复措施、行动项并标记审核，viewer/operator 只能读取。审核保存写入 audit timeline；隔离测试 42 passed，运行中的本机测试数据库已应用迁移，HTTP 登录/读取/越权写入 smoke check 通过，前端脚本语法检查通过。
- 真实 Holmes 模型调查尚未完成：DeepSeek API Key 未设置，不能声称 live RCA 或端到端事故任务通过。
- T006 增量：live runner 先检查 DeepSeek 凭据、Holmes 本机 URL 与健康状态，再向本机 OpenObserve `app_logs` 写入带唯一 run/trace ID 的合成语料并逐例调用 Holmes；缺 DeepSeek Key 时在遥测写入前拒绝运行。三例发布上下文会作为结构化 `release_deployed` 事件写入，关联的 repository Runbook 与来源路径会随对应调查请求传入。seeder/context 单测已通过；实际模型验证仍待 API Key。
- Runbook 增量：Holmes 的自定义技能目录现包含订单库存、数据库迁移不匹配和发布回归三份只读 Skill；评测用例引用相应 Skill。定向测试扫描通过，运行中的 Holmes 容器从实际挂载目录加载到 3 个 Skill。

## 待办

1. 在本机私有运行环境设置有效 `DEEPSEEK_API_KEY` 后，触发真实 500 告警并核对 Holmes 工具调用、持久证据和诊断结果；再运行 20 例 live 评测。密钥不要发送到聊天或写入仓库。
2. 在浏览器按 demo 手册走查工作台登录、不同角色审批、动作执行/恢复与复盘表单；后端 API 权限和复盘写入已在隔离数据库验证。
3. live 调查验收后再运行完整验收；生产平台、身份、密钥、网络、容量/SLO、保留策略和生产动作仍需单独设计与授权。

## 阻塞与授权门槛

- 本机测试 PostgreSQL schema/migration 已获用户明确授权；`0003_incident_retrospectives` 已应用于当前 Docker 测试库。授权不包含正式数据库或任何生产数据。
- 真实 Holmes/模型验收需要 DeepSeek API Key。OpenObserve OSS 没有原生 RBAC；当前本机策略代理和 Docker 网络强制 Holmes 仅能访问两条受限只读路由，但不提供 OpenObserve 原生用户/租户隔离，也不能防止本机 Docker 管理员或代理被攻破。
- 本机 Docker 服务已运行；每次从新 shell 管理 Compose 前需加载本机私有运行变量文件 `/tmp/holmesgpt-aiops-test-runtime.sh`（权限 0600）。该临时文件不在仓库中，系统清理 `/tmp` 后需重新生成配置。
- 当前授权的部署目标是本机 Docker 测试环境。正式部署、生产数据迁移或生产处置需后续分别明确授权；“后续会上正式的”不等于当前授权。

## 最近验证（2026-09-25）

- `docker compose ... config --quiet`、本机栈构建/启动和服务健康检查通过；Holmes `/healthz`、事故 API `/healthz`、OpenObserve `/healthz` 与订单服务返回 HTTP 200。
- 隔离 Compose 测试 profile：42 passed，1 warning。
- 浏览器实际打开 `http://localhost:8081/`，显示事故工作台登录表单且无浏览器 console error；本轮未在浏览器输入账号密码，审批、执行、复盘页面仍以 API/隔离集成测试验证，未记作手工 UI 全流程通过。
- T006 live-eval 数据准备增量：定向 pytest **38 passed**；最新隔离 Compose 测试 profile **46 passed，1 warning**。Mock 报告检查 20 例、`not_scored`、无诊断；缺 DeepSeek Key 时在本机数据写入前退出。此前 OpenObserve 接收 40 条合成记录，Holmes 经只读代理按 trace 查询命中对应日志；没有 DeepSeek 模型调用。结构化发布事件和 Runbook prompt 上下文通过单测，尚未在当前 OpenObserve 卷追加新一轮数据或运行模型。
- 评测报告 schema 升至 1.1：live 每例单独记录是否检索到相同 run/case 的 fixture 行以及 release event，顶层汇总匹配数；报告仍明确把根因诊断 accuracy 标记为 `not_scored`。mock 20 例报告已通过 Draft 2020-12 JSON Schema 校验。
- Holmes 实际挂载的 `/etc/holmes/skills` 由容器内技能加载器识别到 3 个项目 Skill（库存故障、数据库 schema/migration 不匹配、发布回归）；无模型调用。
- 复盘增量：隔离 Compose 测试 profile 42 passed，前端 `node --check` 通过；本机 API 登录后可读取现存事故复盘，operator 写入返回 403；测试数据库记录迁移版本 `0003_incident_retrospectives`。
- 评测语料增量：20 例 mock 报告重生成，检查确认 `not_scored`、20 条病例且无模型诊断；语料/发布-Runbook fixture 校验 **3 passed**，重复 JSON 字段检测测试通过。
- 订单 HTTP webhook 创建持久 incident/task；operator 自审批返回 403，approver 批准后执行状态 ON，再经批准恢复 OFF；最后实测状态为 OFF。
- API、worker、Redis、PostgreSQL 重启后服务恢复，已创建 incident 仍在，最新调查任务安全失败码为 `holmes_unavailable`。
- `pg_dump`/`pg_restore` 恢复到单独 `aiops_restore_test` 数据库成功；恢复库含 1 条 incident，未覆盖活动库或移除数据卷。
- T006 20 例 mock 报告和病例/runbook 引用测试已验证；mock 报告为 `not_scored`，不是 Holmes 诊断质量结果。
- live Holmes/RCA 尚未验收：缺少 DeepSeek 模型凭据；本机 OpenObserve OSS 通过只读策略代理和网络隔离约束 Holmes，但不具备原生 RBAC。

## 详细进度与验证记录

功能清单、已验证细节和外部验收条件见 [`docs/develop-me-roadmap.md`](docs/develop-me-roadmap.md)。
