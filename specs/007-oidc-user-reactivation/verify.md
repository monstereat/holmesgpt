# 验证记录：OIDC 用户受控恢复与会话失效

## 范围与 Diff

- 计划内文件：Incident API 会话认证、用户生命周期、0009 migration、OIDC/workbench 测试、工作台脚本、生产准备说明、README、Roadmap。
- 实际新增涉改文件：`examples/openobserve-aiops/alert-trigger/migrations/0009_user_reactivation.sql`；受控工作流工件 `specs/007-oidc-user-reactivation/**`。
- 范围门禁：T001 pass，T002 pass。
- 无关改动：两份预先存在的 `specs/001-resume-aiops/state-*.json` 未修改。

## 自动验证

| 验证 | 命令／操作 | 结果 | 证据 |
| --- | --- | --- | --- |
| Incident API/workbench 隔离集成套件 | `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test` | pass：123 passed，1 个 Starlette/AnyIO 弃用 warning，21.56s | `evidence/T001-suite-rerun.stdout.log` |
| 前端语法 | `node --check examples/openobserve-aiops/alert-trigger/public/incidents.js` | pass | 状态记录 `T001-js` |
| 本机迁移及 API/worker 重建 | `source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml run --rm incident-migrate && docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build incident-api incident-worker && docker compose -f examples/openobserve-aiops/docker-compose.yaml ps` | pass | `evidence/T002-local-db.stdout.log` |
| 本机服务与数据保留 | 容器 health、`GET /readyz`、PostgreSQL 行数 | API/worker/PostgreSQL/Redis healthy；ready；迁移 9，incidents/tasks/audit/outbox 为 11/11/53/11 | 状态观察 `AC-03-admin-reactivation-test` |
| Git diff whitespace | `git diff --check` | pass | 执行记录 |

首次运行隔离 Compose 测试时没有在外层 shell 加载本地测试环境变量，Compose 因缺少本机 DB 密码变量而未启动测试；随后从权限为 `0600` 的私有文件加载环境并以新的 check ID 重跑，通过。秘密值未打印或写入仓库。

## 结构化证据

- 状态文件：`specs/007-oidc-user-reactivation/state.json`
- Scope 报告：`scope-report-T001.json`、`scope-report-T002.json`
- 命令证据 ID：`T001-suite-rerun`、`T001-js`、`T002-local-db`
- stdout/stderr：`evidence/`
- Git HEAD：`3e7978ce8dfdc34700376ee9faecff8ee8af907a`
- 源码 diff SHA-256（采集时，不含后续 verify artifact）：`688bb995d22e2c0455d0f458a35b6f3da152d5d3447108596cb44172bba7dd7f`

## 验收标准追踪

| AC | 结果 | 验证证据 | 备注 |
| --- | --- | --- | --- |
| AC-01 | pass | `AC-01-generation-test` | 旧 Bearer 与 OIDC cookie 在恢复后仍 401；generation 默认/签名测试通过。 |
| AC-02 | pass | `AC-02-oidc-pending-test` | 重复有效 OIDC callback 只登记一次 pending 请求，不签发 session。 |
| AC-03 | pass | `AC-03-admin-reactivation-test` | 权限/请求体验证、并发审批单次成功、审计 actor/target 与本机运行栈检查通过。 |

## 例外与风险

- 自动测试用模拟 OIDC client 测试重复 callback 行为；签名/JWKS 路径由现有独立 OIDC 测试覆盖。本次未连接真实企业 IdP。
- 停用事务提交前已完成鉴权的在途请求可能完成。
- 滚动混跑旧代码时旧实例不认识 session generation；所有旧 API 实例停止前不得恢复用户。
- 角色/范围仍只由当前 OIDC group mapping 控制；没有本地绕过 IdP 的恢复入口。
- 尚未复跑受 migration 数量硬编码为 8 的离线 backup/role verifiers；这是下一项待办，需要和 0009 对齐后再报告通过。
- 生产平台、生产 IdP、托管 PostgreSQL/Redis 和 staging 网络尚未选定或验收；本次只更改和验证本机 Docker 测试数据库。

## 结论

- 状态：ready_for_review
- 审查人：等待整体 Goal 后续验收
- 人工验收证据：用户授权继续执行 Goal 和本机测试数据库迁移；未对生产环境执行操作。
