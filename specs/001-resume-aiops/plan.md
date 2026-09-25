# 实施 Plan：本机 Docker AIOps 完整测试环境

## 方案摘要

保留 HolmesGPT Python/FastAPI 调查 API 和现有 NestJS order-service。将当前仅用标准库、内存去重并执行 CLI 的 `alert-trigger` 扩展为独立的 Python 事故服务：它负责 webhook、测试身份/RBAC、事故/任务 API、工作台和审批；Celery worker 调用 HolmesGPT 现有 `/api/chat`，不重写 Agent；PostgreSQL 保存事故/任务/审批/审计及 transactional outbox，Redis/Celery 负责任务投递和重试。订单处置只通过 order-service 拥有的受认证测试动作 API 执行。

所有容器加入 `examples/openobserve-aiops/docker-compose.yaml` 同一 Compose 项目。当前目标是本机 Docker 测试环境。Holmes live 调查必须用用户在运行环境提供的模型和 OpenObserve 只读凭据验收；没有凭据时对应验收保持 blocked。不得把秘密写入 `.env`、代码、镜像或报告。

## 重要实现决策

1. **服务边界：** `alert-trigger` 事故服务仅保存和管理事故域状态；Holmes API 继续由根 `server.py` 提供调查能力。Compose 的 Holmes 服务从根构建上下文 `../..`，覆盖根 Dockerfile 默认的 CLI ENTRYPOINT，显式以 `python -u server.py` 启动并通过 `/healthz` 检查。服务间请求在 Compose 内走专用网络，事故服务为唯一的任务幂等事实源。
2. **队列一致性：** 事故和任务状态与 transactional outbox 在同一 PostgreSQL 事务提交；dispatcher 持续扫描并重投未确认 outbox。worker 在 Postgres 原子 claim attempt 后消费，Redis/Celery 重启后由 outbox reconciliation 恢复。Holmes `/api/chat` 没有幂等键；超时后重试按 at-least-once 处理，可能重复模型调用/费用，但事故服务只接受一个最终任务结果，重放不重复创建事故或执行动作。
3. **测试身份：** 事故服务提供仅用于本地测试的角色（viewer/operator/approver/admin），测试用户口令由运行环境提供并用安全哈希存储；服务端对每个对象操作授权。该身份模块不宣称可直接用于正式生产。
4. **测试动作：** order-service 只开放 `set-chaos-mode` 布尔动作，通过自身受认证接口实施。它只改变容器内演示故障状态，不访问 Docker API、宿主机或外部业务数据。事故服务执行前重验用户权限、审批状态和白名单；owner 服务验证资源、参数、服务凭据和幂等键。
5. **恢复：** PostgreSQL 是事故/任务/outbox 持久事实源；dispatcher 定期重投未确认 outbox。worker 在 Postgres 原子 claim attempt 后消费；Redis/Celery 重启后由 outbox reconciliation 恢复。Holmes `/api/chat` 没有幂等键，超时重试按 at-least-once 处理，可能重复模型调用/费用，但事故服务只接受一个最终任务结果。恢复覆盖 Postgres 备份/恢复、API/worker/Redis 重启，不新增备份脚本。
6. **外部依赖：** Git/CI 与 Runbook 先用有来源标识的 fixture 完成关联契约；若提供安全测试端点和凭据，再做 live 集成。现有 20 个样本逐例报告证据、结论、误处置建议、聚类结果和运行模式。

## 文件范围

### 修改

- `examples/openobserve-aiops/docker-compose.yaml`：将 Holmes API、事故 API、Celery worker、PostgreSQL、Redis、OpenObserve 和 order-service 放入同一项目；Holmes 用根 Dockerfile，显式启动 `python -u server.py`，暴露内部 5050 并检查 `/healthz`，只读挂载用户提供的 Holmes 配置目录；加入健康检查、数据卷、内部网络和运行时凭据环境引用。外部端口 loopback 绑定，不挂 Docker socket。
- `examples/openobserve-aiops/alert-trigger/Dockerfile`：固定版本依赖，定义 `runtime` 和 `test` targets，非 root 运行；事故服务测试在隔离容器内运行，不依赖根 Poetry venv 或全局 pip。
- `examples/openobserve-aiops/alert-trigger/trigger.py`：保留兼容入口，改为启动事故 API 应用，不再把 `/bin/echo` 当 Holmes 调查完成结果。
- `examples/openobserve-aiops/alert-trigger/test_trigger.py`：迁移现有 webhook 解析、鉴权、字段边界、去重、错误输出和任务查询测试到持久化服务契约。
- `examples/openobserve-aiops/demo/order-service/src/main.ts`：增加 `set-chaos-mode` 测试动作拥有方接口、服务端认证/白名单/幂等、状态查询与验证端点；保持副作用只在演示服务内。
- `examples/openobserve-aiops/demo/order-service/test/monitoring.test.cjs`：补充动作认证、拒绝、幂等和状态验证断言，或新增动作专用测试文件。
- `examples/openobserve-aiops/README.md`：记录本机架构、认证边界、真实 Holmes live 凭据需求与正式环境未实现项。
- `examples/openobserve-aiops/DEMO.md`：记录启动、故障触发、调查、审批、测试动作、验证/回退、备份恢复及清理数据步骤。
- `examples/openobserve-aiops/evals/README.md`：定义逐案例本地运行及 synthetic/mock/live 结果边界。
- `ROADMAP.md`：每个任务通过验证后更新实际进度、验证命令和阻塞。
- `docs/develop-me-roadmap.md`：同步本机 Docker 阶段进展及生产后续边界。

### 新增

- `examples/openobserve-aiops/alert-trigger/requirements.txt`：事故 API/数据库/队列/认证所需 Python 依赖，固定可复现版本。
- `examples/openobserve-aiops/alert-trigger/requirements-test.txt`：隔离 test 镜像的 pytest 和 HTTP mock 依赖。
- `examples/openobserve-aiops/alert-trigger/app.py`、`auth.py`、`models.py`、`store.py`、`tasks.py`、`worker.py`、`holmes_client.py`、`action_client.py`：事故 HTTP API、测试角色、状态访问、任务/outbox 协调、异步调查和受认证服务调用。
- `examples/openobserve-aiops/alert-trigger/migrations/0001_incidents_tasks.sql`：事故、任务、用户/角色、审批、审计与 outbox schema；只在本机测试 PostgreSQL 使用（该本地迁移已获用户明确授权）。
- `examples/openobserve-aiops/alert-trigger/migrations/0002_task_leases.sql`：扩展任务租约和 outbox 重投时间戳，供 worker 崩溃恢复与 Redis 丢任务后的重复派发；仅应用于本机 Docker 测试数据库。
- `examples/openobserve-aiops/alert-trigger/public/incidents.html`、`incidents.js`：同源本机事故工作台，包含列表/详情/状态/证据/审批/测试动作及加载、空、失败状态。
- `examples/openobserve-aiops/holmes-config/config.yaml.example`：无秘密的 OpenObserve Toolset 示例配置，使用 Compose 服务名、运行时凭据变量、显式 `allowed_streams` 和只读服务账号；真实配置只从用户指定的本机只读目录挂载。
- `examples/openobserve-aiops/alert-trigger/tests/`：认证/权限矩阵、状态机、幂等、outbox、重试恢复、Holmes API 契约、证据解析、动作授权和复盘测试。
- `examples/openobserve-aiops/demo/order-service/test/actions.test.cjs`：受认证动作 API 行为测试。
- `examples/openobserve-aiops/evals/run_evals.py`、`examples/openobserve-aiops/evals/report.schema.json`：运行 20 个 fixture、保存逐例输入证据与结果模式并验证报告结构；live 模式调用真实 Holmes/OpenObserve。
- `specs/001-resume-aiops/tasks.md`、`verify.md`：任务、允许路径、依赖与验证证据记录。

## 执行顺序与验收

### T000：order-service 自有的隔离测试动作接口

- 范围：新增仅支持 `set-chaos-mode` 的内部动作接口；order-service 自身验证服务凭据、固定资源、布尔参数和幂等键，并提供当前状态查询。事故服务后续保存原值，在执行失败时通过 owner 接口恢复。
- AC：AC-07。
- 验证：`node --test examples/openobserve-aiops/demo/order-service/test/actions.test.cjs`。
- 完成定义：未认证、非法动作/参数、错误资源和重复幂等请求均有测试；不访问宿主机、Docker API 或其他服务。

### T001：本地事故域和数据库 schema

- 范围：数据库 schema、事故/用户角色/任务/审批/审计/outbox 模型及状态迁移；身份与权限矩阵；重复 webhook 幂等。
- AC：AC-01、AC-02、AC-06。
- 验证：先构建隔离测试镜像 `docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger`，再执行 `docker run --rm aiops-incident:test python -m pytest -q tests/test_auth.py tests/test_store.py tests/test_incidents.py`。服务依赖在 test target 内安装，不依赖根 Poetry venv 或全局 pip。
- 数据库授权：用户已明确授权仅为本项目本机 Docker 测试卷新增并应用 PostgreSQL schema/migration（2026-09-25）；不得用于正式库或迁移任何现有生产数据。

### T002：持久 webhook、outbox、worker 和重试

- 范围：将告警接收、稳定指纹、事故/任务事务写入、outbox 投递、worker 租约/重试/终态/恢复连接为一条路径；禁止敏感 stderr 入日志。
- AC：AC-01、AC-02、AC-03、AC-08。
- 验证：`docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger` 和 `docker run --rm aiops-incident:test python -m pytest -q tests/test_webhook.py tests/test_tasks.py tests/test_recovery.py`。

### T003：Holmes/OpenObserve 真实只读调查

- 范围：worker 使用 `X-API-Key` 与 `stream:false` 调用现有 Holmes `/api/chat`；task ID 是本地关联字段，不是 Holmes 幂等键。Holmes 没有每请求工具白名单或结构化 evidence DTO；OpenObserve Toolset 在只读挂载的 Holmes 配置中启用，并配置 `allowed_streams`、专用只读账号和服务端 RBAC。事故服务核验 HTTP 状态、`tool_calls[].tool_name`、`result.status` 和工具参数，再映射证据；不凭自然语言认定 verified。
- AC：AC-03、AC-04、AC-09、AC-10。
- 验证：`docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger` 和 `docker run --rm aiops-incident:test python -m pytest -q tests/test_holmes_client.py tests/test_evidence.py`；断言 URL、请求体、API key header、超时和 401/429/5xx/工具错误分类。Live E2E 在 T006 提供入口并由 T007 用 Compose 验收；模型凭据、Holmes config、OpenObserve 专用只读账号/流白名单任一缺失则 AC-04 blocked。

### T004：本机事故工作台

- 范围：事故列表/详情、过滤、状态/时间线、任务尝试、Trace 和证据、审批及执行结果；静态页面由事故 API 同源提供，不在浏览器内放服务秘密。
- AC：AC-05、AC-06、AC-08。
- 验证：`docker run --rm aiops-incident:test python -m pytest -q tests/test_workbench_api.py`；Compose 启动后手动浏览器验收登录及 viewer/operator/approver 的允许/拒绝路径，并记录结果。

### T005：审批、测试动作、执行验证和回退

- 范围：事故审批 API 和工作台的请求/审批/取消/执行交互，用户动作权限复验、白名单/参数校验和幂等 action ID；只调用 T000 的 `set-chaos-mode` owner 接口，以真实 demo 状态验证执行结果，失败时恢复调用前状态并审计。
- AC：AC-05、AC-06、AC-07。
- 验证：`docker run --rm aiops-incident:test python -m pytest -q tests/test_actions.py tests/test_approvals.py`；负向覆盖未认证、越权、未审批、错误资源、重复、取消和验证失败。order-service owner 接口测试由 T000 执行。

### T006：20 例评测与发布/Runbook 关联

- 范围：用逐例 fixture 驱动调查上下文和发布/Runbook 关联；通过共享评测模块供隔离测试镜像和本机 runner 使用；产生 machine-readable report；live 结果要求显式确认并区分检索源与合成案例来源。
- AC：AC-09、AC-10。
- 验证：`docker run --rm aiops-incident:test python -m pytest -q tests/test_evaluation.py`、`python examples/openobserve-aiops/evals/run_evals.py --mode mock` 和 `poetry run pytest -q --no-cov examples/openobserve-aiops/evals/test_known_root_causes.py`；报告逐一覆盖 20 案例且记录 mode。

### T007：Compose 部署、恢复演练、端到端验收和文档

- 范围：加入同一 Compose 项目的 Holmes API、事故 API/worker、Postgres、Redis 和业务服务依赖；健康检查与 loopback 端口。Holmes 从根 Dockerfile 构建并显式运行现有 `server.py`，只读挂载用户配置。通过文档化 `pg_dump`/`pg_restore` 在隔离数据库演练恢复，不新增脚本。
- AC：AC-01 至 AC-11。
- 验证：从当前 shell 临时注入 test-only 值、不写 `.env`，执行 `docker compose -f examples/openobserve-aiops/docker-compose.yaml config --quiet`、`docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build`、容器内 pytest、mock 评测、health/readiness 和浏览器 E2E；重启 Postgres/Redis/worker/API、检查 outbox reconciliation，并用 `pg_dump`/`pg_restore` 恢复到隔离数据库。最后有凭据时运行 live E2E；缺失时不得验收 AC-04 或整体验收。

## 回退策略

- 所有实现仅改 Plan 白名单中的新文件或列出的现有文件；每个任务用独立 signed-off commit 留下可回退点，不 amend、不 push。
- Compose 变更保持原本订单故障、OpenObserve 和 webhook 演示路径可运行；发现旧行为回归时回退对应任务提交，不清理用户数据或运行中的服务卷。
- schema migration 使用前向、带版本的初始化；不得自动删除表或数据。回退通过停止新 API/worker 并恢复旧容器配置完成，不执行破坏性 down migration。
- 数据库恢复仅写入隔离测试数据库，不清理已有 Compose volume；不操作宿主机其他容器、镜像或卷。

## 不在本 Plan 授权范围

- 真实生产发布、数据迁移或任何生产处置。
- 修改 `.env`、凭据、CI/CD 工作流和全局/系统依赖配置。
- 正式数据库 schema/migration；用户仅授权了本项目本机 Docker 测试数据库 schema/migration。
- 在凭据未配置时声称 Holmes live 调查、生产身份、真实 Git/CI/Runbook 服务集成已完成。

## 提交与流程

- `commit_strategy: task`；状态恢复开启。用户明确要求跳过 Plan 独立审查并直接编码。
- 每个任务开始前登记范围 baseline；仅在精确验证通过、范围门禁通过后创建 `git commit -s --no-verify`。
- 不推送远端；本地测试部署完成不代表正式环境发布。
