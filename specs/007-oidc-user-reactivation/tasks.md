# 任务清单：OIDC 用户受控恢复与会话失效

## T001：实现受控身份恢复、迁移和回归测试

- 依赖：无
- 允许文件：`examples/openobserve-aiops/alert-trigger/auth.py`、`models.py`、`app.py`、`migrations/0009_user_reactivation.sql`、`tests/test_auth.py`、`tests/test_oidc_flow.py`、`tests/test_workbench_api.py`、`public/incidents.js`、`examples/openobserve-aiops/PRODUCTION-READINESS.md`、`examples/openobserve-aiops/README.md`、`ROADMAP.md`
- 操作：实现 generation 检查、OIDC pending 申请、管理员恢复 API/工作台、迁移和安全/并发测试。
- 对应 AC：AC-01、AC-02、AC-03
- 验证：`docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test`；`node --check examples/openobserve-aiops/alert-trigger/public/incidents.js`
- 状态机验证命令：分别以精确命令登记在 `add-task --command`。
- 范围基线：`specs/007-oidc-user-reactivation/scope-baseline-T001.json`
- 范围报告：`specs/007-oidc-user-reactivation/scope-report-T001.json`
- 完成定义：隔离 PostgreSQL 全套测试与 JS 语法检查通过，scope gate 通过。
- 状态：pending
- 证据 ID：`T001-suite`、`T001-js`

## T002：迁移当前本机测试数据库并验证服务

- 依赖：T001
- 允许文件：`specs/007-oidc-user-reactivation/verify.md`、`specs/007-oidc-user-reactivation/**`
- 操作：在用户授权的本机 Docker 测试 PostgreSQL 上应用新迁移，不清理既有数据；重建 incident API/worker 并核验健康状态、迁移版本和现有行数。
- 对应 AC：AC-01、AC-02、AC-03
- 验证：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml run --rm incident-migrate && docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build incident-api incident-worker && docker compose -f examples/openobserve-aiops/docker-compose.yaml ps`
- 状态机验证命令：`add-task --command` 登记的精确命令。
- 范围基线：`specs/007-oidc-user-reactivation/scope-baseline-T002.json`
- 范围报告：`specs/007-oidc-user-reactivation/scope-report-T002.json`
- 完成定义：migration gate 成功，迁移为 0009，API/worker healthy，数据行数保留，`/readyz` 为 ready。
- 状态：pending
- 证据 ID：`T002-local-db`
