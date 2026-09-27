# 实施计划：OIDC 用户受控恢复与会话失效

## 已确认上下文

- 相关模块：`alert-trigger/auth.py` 的有签名会话、`app.py` 的 OIDC callback 与用户管理、迁移 0001–0008、`public/incidents.js` 的管理员用户表。
- 现有行为：停用用户 active=false 后每请求拒绝会话；OIDC callback 更新 IdP 映射但不恢复账户；没有管理员恢复接口。
- 安全约束：停用时递增用户级 session generation；签发 token 带 generation，鉴权时与数据库比较；恢复只能针对有效 OIDC callback 已登记的 pending 请求。

## 方案

- 数据流：新增 `users.session_generation` 和 `users.reactivation_requested_at`；OIDC callback 在数据库事务中更新当前身份映射。若账户停用，则仅首次设置 pending 时间并追加 `user.reactivation_requested` 审计，然后返回 403 且不签发 session。
- 会话边界：签发 session 写入 `sg`，解析成 Principal.session_generation；遗留 token 将 `sg` 视作 0。每个请求要求 token generation 等于当前用户值且用户 active。停用时 generation +1。
- 管理接口：`GET /api/users` 显示恢复 pending；`POST /api/users/{id}/reactivate` 要求 user:manage、目标为停用且 pending 的用户，只更改 active/pending 时间并审计，不接收角色/范围字段。
- 兼容/回退：新增 0009 向前迁移，保留既有行默认 generation 0；代码回退时需保留迁移列，新代码可继续容忍旧 token；无需删除/清库。

## 文件清单

| 文件 | 操作 | 原因 | 对应 AC |
| --- | --- | --- | --- |
| `examples/openobserve-aiops/alert-trigger/auth.py` | modify | JWT 载入/返回 session generation | AC-01 |
| `examples/openobserve-aiops/alert-trigger/models.py` | modify | Principal 保存 generation | AC-01 |
| `examples/openobserve-aiops/alert-trigger/app.py` | modify | OIDC 请求、鉴权、停用递增、管理员恢复 API | AC-01–03 |
| `examples/openobserve-aiops/alert-trigger/migrations/0009_user_reactivation.sql` | add | 持久化 generation 与待审核时间 | AC-01–03 |
| `examples/openobserve-aiops/alert-trigger/tests/test_auth.py` | modify | token generation 的兼容性与解析 | AC-01 |
| `examples/openobserve-aiops/alert-trigger/tests/test_oidc_flow.py` | modify | 停用 OIDC callback 申请及拒绝 session | AC-02 |
| `examples/openobserve-aiops/alert-trigger/tests/test_workbench_api.py` | modify | 恢复 RBAC、状态、旧 session、审计 | AC-01–03 |
| `examples/openobserve-aiops/alert-trigger/public/incidents.js` | modify | 管理员显示恢复申请并操作 | AC-03 |
| `examples/openobserve-aiops/PRODUCTION-READINESS.md` | modify | 同步生命周期和部署迁移说明 | AC-01–03 |
| `examples/openobserve-aiops/README.md` | modify | 说明本机身份恢复流程 | AC-02–03 |
| `ROADMAP.md` | modify | 记录已验证的增量和边界 | AC-01–03 |

## 执行顺序

1. 新增迁移和 generation 会话校验，并加入 unit/auth 与隔离 PostgreSQL 生命周期测试。
2. 实现 OIDC pending 与受限恢复 API，再完善工作台和定向测试。
3. 迁移当前本机测试数据库，重建服务并运行完整 incident suite、OIDC 流程和前端语法检查。
4. 更新生产准备文档和 Roadmap，记录实际迁移/健康检查证据与目标平台缺口。

## 验证计划

| 层级 | 命令或操作 | 通过条件 |
| --- | --- | --- |
| 定向测试 | `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python -m pytest -q tests/test_auth.py tests/test_oidc_flow.py tests/test_workbench_api.py` | 退出码 0 |
| 前端语法 | `node --check examples/openobserve-aiops/alert-trigger/public/incidents.js` | 退出码 0 |
| 全套测试 | `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test` | 全部通过 |
| 本机 DB 迁移 | 按 `POSTGRESQL-OPERATIONS.md` 运行 migration gate，检查 0009 与原有行数保留 | migration job 成功，既有数据不变 |
| 本机服务 | 检查 incident API/worker health、`/readyz` 与工作台 API 流程 | 服务 healthy，恢复和旧 token 拒绝符合预期 |

## 执行控制

- 状态恢复：on
- 独立审查：spec
- 分支策略：当前分支 develop-me
- 提交策略：task，遵守项目 DCO signed-off commit 约束

## 状态机注册

- 已注册 AC：AC-01、AC-02、AC-03
- 已注册任务：T001
- 状态文件：`specs/007-oidc-user-reactivation/state.json`

## 风险与回退

- 停用时 generation 更新和 active=false 必须是同一 UPDATE/事务，否则可能保留可用会话。
- 迁移只加列；回退应用代码时保留列，旧代码不读取新字段，不执行 down migration。
- 新 OIDC login 对 disabled user 返回 403 是预期；用户表 pending 显示不得暴露给非管理员。
- 本次仅运行于本机测试数据库，不验证生产 IdP 或正式部署。

## 审批

- Plan 状态：approved
- 批准人：monstereat
- 批准时间：2026-09-27
- 批准证据：用户明确要求继续完成现有 Goal 并授权所需操作；此前明确授权本机测试迁移并要求按文档直接编码。
