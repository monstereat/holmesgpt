# Tasks：本机 Docker AIOps 测试环境

> 用户已授权本机测试 PostgreSQL schema/migration；只限该 Compose 项目测试卷。计划直接编码，用户要求跳过独立审查。每项任务仍须按其范围验证并更新 `ROADMAP.md`。

## T000：order-service 自有的隔离测试动作接口

- 依赖：无
- 对应 AC：AC-07
- 允许改动：`examples/openobserve-aiops/demo/order-service/src/main.ts`、`examples/openobserve-aiops/demo/order-service/test/actions.test.cjs`、`ROADMAP.md`。
- 操作：添加仅支持 `set-chaos-mode` 的受认证接口和状态验证端点。order-service 自身校验服务凭据、固定资源、bool 参数及幂等键；副作用只改变 demo 内存状态。
- 验证命令：`node --test examples/openobserve-aiops/demo/order-service/test/actions.test.cjs`
- 完成定义：未认证、非法动作/参数、错误资源、幂等重放均通过断言；无宿主机、Docker API 或其他资源写通道；ROADMAP 记录结果。

## T001：本地事故域、身份与数据库 schema

- 依赖：T000
- 对应 AC：AC-01、AC-02、AC-06
- 允许改动：`examples/openobserve-aiops/alert-trigger/app.py`、`auth.py`、`models.py`、`store.py`、`migrations/**`、`requirements.txt`、`requirements-test.txt`、`Dockerfile`、`tests/test_auth.py`、`tests/test_store.py`、`tests/test_incidents.py`、`ROADMAP.md`。
- 操作：创建事故、任务、用户/角色、审批、审计及 outbox schema/model；实现本地测试角色和资源授权；稳定 webhook 指纹使用数据库唯一约束。
- 验证命令：`docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger && docker run --rm aiops-incident:test python -m pytest -q tests/test_auth.py tests/test_store.py tests/test_incidents.py`
- 完成定义：未认证/低权限/跨资源访问拒绝；重复事件只创建一个事故/任务；事务回滚无部分记录；授权只用于本地测试数据库，不触碰正式库；ROADMAP 记录结果。

## T002：Webhook、outbox、worker 与重试恢复

- 依赖：T001
- 对应 AC：AC-01、AC-02、AC-03、AC-08
- 允许改动：`examples/openobserve-aiops/alert-trigger/trigger.py`、`app.py`、`tasks.py`、`worker.py`、`migrations/**`、`Dockerfile`、`requirements.txt`、`requirements-test.txt`、`test_trigger.py`、`tests/test_webhook.py`、`tests/test_tasks.py`、`tests/test_recovery.py`、`docker-compose.yaml`、`ROADMAP.md`。
- 操作：webhook 在 Postgres 同一事务写事故/任务/outbox；dispatcher 持续重投；worker 在 DB 原子 claim、设置重试/超时/终态。Redis/Celery 丢任务后可经 outbox reconciliation 恢复；敏感 stderr 不入记录。
- 验证命令：`docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger && docker run --rm aiops-incident:test python -m pytest -q tests/test_webhook.py tests/test_tasks.py tests/test_recovery.py`
- 完成定义：未授权/非法 webhook 不排队；重复事件幂等；瞬时/永久失败分别按预算重试和终止；API/worker/Redis 重启后状态恢复；ROADMAP 记录结果。

## T003：Holmes/OpenObserve 只读调查客户端

- 依赖：T002
- 对应 AC：AC-03、AC-04、AC-09、AC-10
- 允许改动：`examples/openobserve-aiops/alert-trigger/holmes_client.py`、`app.py`、`worker.py`、`tests/test_holmes_client.py`、`tests/test_evidence.py`、`examples/openobserve-aiops/holmes-config/config.yaml.example`、`examples/openobserve-aiops/README.md`、`examples/openobserve-aiops/skills/order-service-inventory-failure/SKILL.md`、`ROADMAP.md`。
- 操作：以 `X-API-Key` 和 `stream:false` 调现有 Holmes `/api/chat`；task ID 仅作本地关联，重试幂等由事故服务保证。Holmes 服务端只启用 OpenObserve Toolset，使用显式 `allowed_streams` 和专用只读账号/RBAC；核验 HTTP 状态及 `tool_calls[].result` 后映射证据。
- 验证命令：`docker build --target test -t aiops-incident:test examples/openobserve-aiops/alert-trigger && docker run --rm aiops-incident:test python -m pytest -q tests/test_holmes_client.py tests/test_evidence.py`
- 完成定义：测试精确 URL/body/header、401/429/5xx/timeout/tool error；模型文本不单独作为 verified 证据。live E2E 在 T007 验收；缺模型和 OpenObserve RO 凭据时 AC-04 保持 blocked；ROADMAP 记录结果。

## T004：本机事故工作台

- 依赖：T001、T002、T003
- 对应 AC：AC-05、AC-06、AC-08
- 允许改动：`examples/openobserve-aiops/alert-trigger/app.py`、`public/incidents.html`、`public/incidents.js`、`tests/test_workbench_api.py`、`ROADMAP.md`。
- 操作：呈现事故列表/详情、状态、任务重试、时间线、Trace/证据、审批和执行结果；实现清晰的 loading/empty/error 状态；页面不持有服务端密钥。
- 验证命令：`docker run --rm aiops-incident:test python -m pytest -q tests/test_workbench_api.py`
- 完成定义：API 角色授权测试通过；本机浏览器验收登录和 viewer/operator/approver 允许/拒绝路径，记录在 `verify.md`；ROADMAP 更新。

## T005：审批、受限测试动作、验证和回退

- 依赖：T000、T001、T002、T004
- 对应 AC：AC-05、AC-06、AC-07
- 允许改动：`examples/openobserve-aiops/alert-trigger/app.py`、`action_client.py`、`tasks.py`、`public/incidents.js`、`tests/test_actions.py`、`tests/test_approvals.py`、`docker-compose.yaml`、`ROADMAP.md`。
- 操作：服务端复验身份/资源/审批/白名单/参数/幂等键；执行仅调用 order-service owner 接口；保存原 demo 状态以供验证失败时回退，完整记录审计。
- 验证命令：`docker run --rm aiops-incident:test python -m pytest -q tests/test_actions.py tests/test_approvals.py`
- 完成定义：未认证、越权、未审批、错误资源、重复、取消和验证失败路径通过；执行器无直接 DB、Docker API、任意 shell 写路径；ROADMAP 更新。

## T006：20 例评测与发布/Runbook fixture

- 依赖：T003
- 对应 AC：AC-09、AC-10
- 允许改动：`examples/openobserve-aiops/evals/README.md`、`run_evals.py`、`report.schema.json`、`test_known_root_causes.py`、`examples/openobserve-aiops/alert-trigger/evaluation.py`、`examples/openobserve-aiops/alert-trigger/tests/test_evaluation.py`、`examples/openobserve-aiops/runbooks/**`、`examples/openobserve-aiops/skills/**`、`ROADMAP.md`。
- 操作：逐例记录来源、运行模式、输入、检索证据、诊断、假设、误处置建议及聚类结果；发布和 Runbook fixture 保留来源标识；不伪造不存在的来源或指标。
- 验证命令：`docker run --rm aiops-incident:test python -m pytest -q tests/test_evaluation.py && python3 examples/openobserve-aiops/evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json && docker run --rm -v "$PWD/examples/openobserve-aiops/evals:/project/examples/openobserve-aiops/evals:ro" -v "$PWD/examples/openobserve-aiops/runbooks:/project/examples/openobserve-aiops/runbooks:ro" -v "$PWD/examples/openobserve-aiops/skills:/project/examples/openobserve-aiops/skills:ro" -w /project aiops-incident:test python -m pytest -q --confcutdir=examples/openobserve-aiops/evals examples/openobserve-aiops/evals/test_known_root_causes.py`
- 完成定义：20 案例在机器可读报告逐条出现，synthetic/mock/live 标签正确；ROADMAP 更新。

## T007：Compose 部署、恢复演练、端到端和文档

- 依赖：T000、T001、T002、T003、T004、T005、T006
- 对应 AC：AC-01 至 AC-11
- 允许改动：`examples/openobserve-aiops/docker-compose.yaml`、`examples/openobserve-aiops/README.md`、`DEMO.md`、`evals/README.md`、`ROADMAP.md`、`docs/develop-me-roadmap.md`、`specs/001-resume-aiops/verify.md`。
- 操作：同一个 Compose project 运行 Holmes API（现有 `server.py`）、事故 API/worker、Postgres、Redis、OpenObserve 和 order-service。Holmes 服务通过根 Dockerfile build context `../..` 构建，显式 `python -u server.py` 启动、端口 5050、healthcheck `/healthz`；用户配置目录只读挂载。文档化 `pg_dump`/`pg_restore` 恢复到隔离测试 DB；不清理旧 volume。
- 验证命令：使用 `/tmp/holmesgpt-aiops-test-runtime.sh` 中的本机测试变量（不写 `.env`），运行 `docker compose -f examples/openobserve-aiops/docker-compose.yaml config --quiet`、`docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build`、`docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test` 和 `python3 examples/openobserve-aiops/evals/run_evals.py --mode mock --output /tmp/holmes-aiops-mock-report.json`；另核对服务 health、浏览器/API E2E、API/worker/Redis/Postgres 重启后的 outbox 恢复及隔离数据库备份恢复。不能用健康检查代替 live Holmes 调查验收。
- 完成定义：故障→live Holmes→证据→审批→demo 动作→验证/回退→复盘完整闭环。缺少模型、Holmes config 或 OpenObserve 只读账户/白名单时，AC-04 和整体验收保持 blocked/incomplete；ROADMAP 记录真实结果和阻塞。
