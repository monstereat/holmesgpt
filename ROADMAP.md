# HolmesGPT AIOps 项目路线图

## 当前阶段

**本机 Docker 测试环境优先：T000–T009 已部署，真实 Holmes 接入和 20 案 live 评测已跑通；诊断准确率尚未人工评分。** Holmes 默认使用 `deepseek/deepseek-flash`，以 `MODEL` 和 `DEEPSEEK_API_KEY` 配置；每次调查限制为 12 个模型步骤。最新复核报告含 20 案、40 条合成记录、20/20 当前 run/case 证据查询命中、3/3 发布事件命中和 0 个工具错误；查询覆盖按实际 SQL 过滤、非空结果和时间范围核验。scoring 仍为 `not_scored`。Holmes 只能经受限的流列表/搜索端点访问 allowlist 流。OpenObserve OSS 自身仍不提供原生 RBAC；代理只收窄 Holmes 的访问路径。用户授权仅覆盖本机测试 schema/migration 和测试部署，不包含正式环境。

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
- T003：Holmes 非流式 `/api/chat` 客户端、状态码与工具结果校验、限定流范围的证据映射和敏感值脱敏已完成；21 项 Holmes/evaluation 契约测试通过。真实 worker 调查已完成并持久化 13 条已验证证据；JSON 字符串型工具数据现会先解析为结构化记录再做评测匹配。
- T004：工作台提供本地登录、服务端角色/资源校验、事故/任务/Trace/证据/审批/审计详情和受限任务重试。一次性本机 PostgreSQL 集成测试 2 passed；浏览器手工登录 operator 与 approver 并检查角色差异。
- T005：工作台可申请/批准/拒绝/取消及执行测试动作；operator 与 approver 分权，执行前复验审批和固定白名单，调用 order-service owner 接口并核对实际状态，失败时恢复原状态并审计。一次性本机 PostgreSQL 集成测试 4 passed；`node --check` 和 Compose 配置校验通过；浏览器走通独立审批的 ON→OFF 动作闭环并核对审计记录。
- T006：评测 runner 可生成 20 例 mock/live 结构化报告；mock 不生成模型诊断且标记 `not_scored`，live 请求需显式 `--confirm-live`，病例来源与实时 OpenObserve 证据分别标记。三条 synthetic 发布上下文引用病例证据并关联 Runbook。此前报告曾显示 14/20 病例证据匹配；后续发现 Holmes 查询虽有精确 run/case SQL 条件，但投影列未必包含 ID，旧计数因此低估检索覆盖。当前修正后的报告见本页 T006 增量记录；诊断评分仍为 `not_scored`，不能据此声明准确率。
- T007：Holmes、事故 API/worker、PostgreSQL、Redis、OpenObserve 和 order-service 已在一个 Compose project 运行；隔离测试基线 42 项通过，最新全套 Compose profile 46 项通过。已实测告警→持久化任务、权限分离审批、demo 动作执行/回滚、API/worker/Redis/PostgreSQL 重启恢复及独立数据库备份/恢复。Holmes 默认 `deepseek/deepseek-flash` 已通过真实模型调用；Compose 映射为 Holmes `MODEL` 和 LiteLLM `DEEPSEEK_API_KEY`。每次调查配置 12 步上限。OpenObserve OSS 无服务端 RBAC，生产身份/租户隔离需另选具备 RBAC 的部署方案。
- T008：加入固定上游、独立 Basic Auth、仅 streams/search 两条路由、SQLGlot ClickHouse AST、流 allowlist、单小时查询窗、超时/行数/请求和响应大小上限；Holmes 未加入 OpenObserve 或 telemetry 网络。11 项代理策略单测通过。Holmes 容器无法解析 OpenObserve 服务名，通过代理可见 2 条 allowlist 流并实际查询到日志。本机 OpenObserve UI 仍只绑定 loopback。
- T009：新增本机迁移 `0003_incident_retrospectives` 和按事故持久化的复盘 API/工作台表单；approver/admin 可保存影响、根因、恢复措施、行动项并标记审核，viewer/operator 只能读取。审核保存写入 audit timeline；隔离测试 42 passed，运行中的本机测试数据库已应用迁移，HTTP 登录/读取/越权写入 smoke check 通过，前端脚本语法检查通过。
- 生产准备：新增平台无关的 [`examples/openobserve-aiops/PRODUCTION-READINESS.md`](examples/openobserve-aiops/PRODUCTION-READINESS.md)，列出平台/身份/密钥/网络/数据服务等待确认输入、部署配置安全门槛、staging 验收和 go/no-go 证据。尚无可执行的生产 manifest，正式部署与生产操作仍不在当前授权范围。
- 2026-09-25 生产准备审计：仓库 Helm chart 只部署 Holmes API，不包含 AIOps 示例的 incident API、worker、workbench、PostgreSQL、Redis、OpenObserve 及策略代理；示例只有本机 Docker Compose。当前开发机没有配置 Kubernetes context。生产平台和部署范围待用户确认；provider-neutral OIDC 已加入代码，但真实生产 IdP 尚未配置或验收。
- 2026-09-25 身份边界复核：管理员角色虽声明 `user:manage`，但没有用户管理 API；旧版工作台仅支持本机测试用户。运行中本机 Compose 的 Holmes、incident API/worker、代理、订单服务、OpenObserve、PostgreSQL、Redis 均为 healthy/up；该状态仅证明本机测试栈，不证明生产准备完成。
- 生产准备增量：把 incident API 启动期 schema migration 拆成 `migration_runner.py` / `migrate.py` 一次性迁移命令；本机 Compose 的 `incident-migrate` 必须成功退出后 API 和 worker 才启动。测试身份仅在显式 `AIOPS_ENV=local` 时可注入，其他环境误配置会拒绝启动。演示 `set-chaos-mode` 处置默认关闭，本机 Compose 显式启用；关闭时 API 拒绝新建和执行演示动作。Incident API 增加 `/readyz` 检查 PostgreSQL，Compose 将其作为 API 容器健康探针；worker 健康探针要求 Celery 经 Redis 返回 `pong`，重建后的容器状态为 `healthy`。之前的隔离 Compose 验证为 **54 passed，1 warning**；本机 migration 容器现已应用至 0004，API `/healthz` 和 `/readyz` 返回成功。生产仍需目标平台 migration Job/release gate、独立低权限 migration 身份及队列/延迟告警。
- 2026-09-25 OIDC 身份增量：新增生产默认 OIDC 模式、授权码 + PKCE S256、数据库一次性 state/nonce/verifier、Authlib 验签与 nonce 校验、固定 issuer/回调源、显式 group→role/resource-scope 映射、15 分钟 HttpOnly Cookie 和同源写请求校验；本地口令登录仅允许 `AIOPS_ENV=local`。新增迁移 `0004_oidc_identities.sql` 和真实 RSA/JWKS 本地签名提供方测试。身份相关测试 **19 passed，1 warning**。尚未接真实 IdP；单发行方、多租户、用户管理/紧急恢复、IdP 生命周期和生产迁移 Job 仍未完成。配置/风险/变量合同见 [`examples/openobserve-aiops/PRODUCTION-READINESS.md`](examples/openobserve-aiops/PRODUCTION-READINESS.md)。
- 2026-09-25 本机 500 告警链路复核：启用本机 chaos mode 后，真实订单 HTTP 请求返回 500，带同一 Trace ID 的 webhook 创建了持久事故/任务；因新 shell 未载入私有 DeepSeek 环境文件，首次调查安全失败。第一轮受限重试验证了 12 条 allowlist 证据，但模型返回不可读工具协议，暴露出“非空分析即完成”的缺口。根因定位为模型自行扩展的查询窗口越过只读代理的未来时间上限，OpenObserve 返回 400。现由服务端按 webhook 告警时间生成固定的 -5 分钟至 +1 分钟窗口，并增加拒绝协议标记分析的完整性校验。第二轮人工重试只用 2 次 OpenObserve 查询，查到精确 Trace 的 2 条日志，得到可读诊断；task 状态 completed，审计包含 failed、manual_retry_requested、completed。Compose Holmes 健康检查要求 DeepSeek Key 存在且 `/readyz` 成功，worker 等待 Holmes healthy；远程 Key/服务可用性仍受外部提供商影响。chaos mode 已恢复 off，`/healthz`、`/readyz` 返回 200，普通订单恢复 HTTP 201。incident 隔离测试 **70 passed，1 warning**。这是本机合成场景的链路证据，不等于生产可用性或诊断准确率。
- 身份生命周期增量：新增 admin-only 的用户映射列表和停用接口，并在事故工作台加入对应操作。服务端每次请求重新检查用户 active 状态，因此停用会立即使已有会话失效；停用操作写入 audit，不能停用自己的账户，也不能移除最后一名 active admin。身份角色仍仅由 IdP group mapping 分配；无角色编辑/重激活接口。隔离 PostgreSQL 集成测试覆盖 RBAC、会话立即失效、重复停用和审计；当前全套测试 **70 passed，1 warning**，workbench JS `node --check` 通过。未接真实 IdP。
- 会话退出增量：新增 `0005_revoked_sessions.sql`，只存已退出 session token 的 SHA-256 与到期时间；`/auth/logout` 同时处理 OIDC cookie 和本地 Bearer token，API 每次鉴权查询撤销表，工作台只有在服务端撤销成功后才清除本地令牌。每个令牌加入随机 JTI，避免同一秒重复登录复用已撤销 token。隔离 PostgreSQL 测试覆盖 OIDC cookie / Bearer logout、撤销后的拒绝及同秒重登；全套测试 **70 passed，1 warning**。迁移仅通过本机 Compose migration gate 应用到获授权的本机测试数据库，生产仍需独立 Job 与 least-privilege migration identity。
- 事故 worker 通过持久任务调用 DeepSeek 的端到端验证已完成；这证明任务链路和证据持久化可用，但不会替代诊断质量人工评分。
- T006 增量：live runner 检查 DeepSeek 凭据、Holmes 本机 URL 与健康状态，再向本机 OpenObserve 写入唯一 run/trace ID 的合成语料并逐例调用 Holmes。真实 20 案运行已完成；发布上下文以结构化事件写入，Runbook 来源随请求传入。报告计分保持 `not_scored`，并分别核验病例证据与发布事件匹配。
- T006 增量：OpenObserve ingest 全量接受后，runner 按唯一 run ID 等待 `_search` 查到所有行（最多 30 秒）；合成 alert 时间设在记录时间后 1 秒，Holmes 提示提供精确 run/case 锚点和 ±60 秒窗口。Live 计分器现按 SQL 精确 run+case 过滤、非空 hits 和覆盖 seed 时间窗判定，即使 SELECT 投影没有返回过滤列也可正确计数；release match 还要求同一 scope 命中 release event。Seeder/client/evaluation 定向测试 **41 passed**。20 案 live 报告复核为 20/20 案例证据查询命中、3/3 发布事件命中、0 个 Holmes 工具错误；报告 schema 1.2 校验通过，诊断评分仍为 `not_scored`。复核版 `/tmp/holmes-aiops-live-report-scoped-run-reviewed.json`，原始 live 输出 `/tmp/holmes-aiops-live-report-scoped-run.json`。更早报告的计数器未处理投影列缺失，已由复核报告替代。
- Runbook 增量：Holmes 的自定义技能目录现包含订单库存、数据库迁移不匹配和发布回归三份只读 Skill；评测用例引用相应 Skill。定向测试扫描通过，运行中的 Holmes 容器从实际挂载目录加载到 3 个 Skill。

## 待办

1. 用本机告警入口和持久任务 worker 完成一次真实 500 告警端到端调查，核对调查状态、工具证据和审计记录；不得把模型诊断文案直接当成根因准确性评分。
2. 建立独立于检索覆盖率的根因诊断评分 rubric，并人工评分 live 案例；完成前维持 `not_scored`。
3. 生产部署决策与分阶段验收见 [`examples/openobserve-aiops/PRODUCTION-READINESS.md`](examples/openobserve-aiops/PRODUCTION-READINESS.md)，其中平台、身份、密钥、网络、容量/SLO、保留策略和生产动作仍待负责人确认与单独授权。

## 阻塞与授权门槛

- 本机测试 PostgreSQL schema/migration 已获用户明确授权；`0003_incident_retrospectives` 已应用于当前 Docker 测试库。授权不包含正式数据库或任何生产数据。
- DeepSeek 凭据已配置在本机私有文件中，没有进入仓库。OpenObserve OSS 没有原生 RBAC；当前本机策略代理和 Docker 网络强制 Holmes 仅能访问两条受限只读路由，但不提供 OpenObserve 原生用户/租户隔离，也不能防止本机 Docker 管理员或代理被攻破。
- 本机 Docker 服务已运行；每次从新 shell 管理 Compose 前需加载本机私有运行变量文件 `/tmp/holmesgpt-aiops-test-runtime.sh`（权限 0600）。该临时文件不在仓库中，系统清理 `/tmp` 后需重新生成配置。
- 当前授权的部署目标是本机 Docker 测试环境。正式部署、生产数据迁移或生产处置需后续分别明确授权；“后续会上正式的”不等于当前授权。

## 最近验证（2026-09-25）

- OIDC 增量：隔离 Compose 全套测试 **70 passed，1 warning**；其中 19 项身份定向测试包含本地 RSA/JWKS 签名提供方的 Authlib 回调验证。已在获授权的本机 Docker 测试数据库执行迁移，`schema_migrations` 登记 0001–0004；重建 incident API/worker 后容器均为 healthy，API `/healthz`、`/readyz`、`/auth/mode` 返回成功，浏览器登录模式为 local；前端 `node --check` 和 `docker compose config --quiet` 通过。真实企业 IdP 和生产流量尚未测试。

- `docker compose ... config --quiet`、本机栈构建/启动和服务健康检查通过；Holmes `/healthz`、事故 API `/healthz`、OpenObserve `/healthz` 与订单服务返回 HTTP 200。
- 隔离 Compose 测试 profile：42 passed，1 warning。
- 浏览器手工验收 `http://localhost:8081/`：分别以 operator 和 approver 登录；operator 发起演示动作，approver 批准，operator 执行并由 owner 核验 OFF→ON；随后申请并批准恢复，由 operator 执行且核验 ON→OFF。approver 成功保存并审核合成事故复盘，审计时间线记录审批、动作验证和复盘审核；operator 页面不显示复盘写入/审核按钮，approver 页面显示；浏览器 console 无 error。此前无 Key 时合成告警曾安全终止为 `holmes_unavailable`。
- T006 live-eval 数据准备增量：此前定向 pytest **38 passed**，隔离 Compose profile **46 passed，1 warning**。现已实际运行 live 评测：本次 OpenObserve 接收 40 条带唯一运行标识的合成记录，20/20 请求成功返回诊断，记录到 217 条工具证据；14/20 案例精确命中本轮记录，3/3 发布事件命中。原始工具数据以 JSON 字符串返回，修复结构化解析后从本次真实响应重算匹配指标；报告 schema 1.2 通过验证，diagnosis scoring 仍为 `not_scored`。
- 评测报告 schema 升至 1.1：live 每例单独记录是否检索到相同 run/case 的 fixture 行以及 release event，顶层汇总匹配数；报告仍明确把根因诊断 accuracy 标记为 `not_scored`。mock 20 例报告已通过 Draft 2020-12 JSON Schema 校验。
- 评测报告 schema 升至 1.2：参考预期、参考不支持结论和参考安全建议使用 `reference_*` 字段；未从自由文本分析提取的 assumptions 为 `null`，不再用空数组暗示“没有假设”。评测定向测试 **11 passed**，20 例 mock 报告通过 Draft 2020-12 Schema 校验；重建 Compose 测试镜像后隔离测试 **46 passed，1 warning**。这仍不提供诊断评分，`not_scored` 保持。
- Holmes 实际挂载的 `/etc/holmes/skills` 由容器内技能加载器识别到 3 个项目 Skill（库存故障、数据库 schema/migration 不匹配、发布回归）；无模型调用。
- 全仓非 LLM 回归最新重跑：**3861 passed、160 skipped、0 failed、119 warnings**。Internet Toolset 现在不继承环境代理或 `.netrc`，保持对已验证 IP 的直连。AIOps 隔离 Compose 测试 profile 最近一次 **46 passed，1 warning**。本轮 Holmes/DeepSeek 配置测试 **15 passed**；调查客户端和评测契约测试 **21 passed**。
- 本轮重新生成 20 例 mock 报告并用项目 Poetry Python 3.12 的 Draft 2020-12 validator 校验通过；20 例均无模型诊断且 `not_scored`。系统 Python 缺 `jsonschema`，因此使用项目环境完成校验；这不构成 live 诊断质量结果。
- 复盘增量：隔离 Compose 测试 profile 42 passed，前端 `node --check` 通过；本机 API 登录后可读取现存事故复盘，operator 写入返回 403；测试数据库记录迁移版本 `0003_incident_retrospectives`。
- 评测语料增量：20 例 mock 报告重生成，检查确认 `not_scored`、20 条病例且无模型诊断；语料/发布-Runbook fixture 校验 **3 passed**，重复 JSON 字段检测测试通过。
- 订单 HTTP webhook 创建持久 incident/task；operator 自审批返回 403，approver 批准后执行状态 ON，再经批准恢复 OFF；最后实测状态为 OFF。
- API、worker、Redis、PostgreSQL 重启后服务恢复，已创建 incident 仍在。早期无模型密钥的任务以 `holmes_unavailable` 安全失败；本轮修复配置后，持久任务手动重试成功并保存 verified 证据。
- `pg_dump`/`pg_restore` 恢复到单独 `aiops_restore_test` 数据库成功；恢复库含 1 条 incident，未覆盖活动库或移除数据卷。
- T006 20 例 mock 报告和病例/runbook 引用测试已验证；mock 报告为 `not_scored`，不是 Holmes 诊断质量结果。
- 历史 20 案 live Holmes 评测报告曾记录 14/20 病例证据匹配、3/3 发布事件匹配、0 个调用错误；`scoring` 保持 `not_scored`。后续按查询过滤条件复核并修正计量方式，当前数值见上方最新增量。实际告警 worker 也已通过：webhook 创建持久事故和任务，授权手动重试后任务以一次 attempt 完成，保存 13 条已验证证据并写入 5 条任务审计事件。本机 OpenObserve OSS 通过只读代理和网络隔离约束 Holmes，但不具备原生 RBAC。
- 2026-09-25 T006 live 计量复核：本机 OpenObserve 两行 smoke test 2/2 可搜索；40 条 seed 的 20 案运行确认 20/20 exact run/case 搜索与 3/3 release event 搜索，0 工具错误。诊断评分保持 `not_scored`。复核报告 `/tmp/holmes-aiops-live-report-scoped-run-reviewed.json`。

## 详细进度与验证记录

功能清单、已验证细节和外部验收条件见 [`docs/develop-me-roadmap.md`](docs/develop-me-roadmap.md)。
