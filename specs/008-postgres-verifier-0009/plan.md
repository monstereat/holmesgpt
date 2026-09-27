# 实施计划：PostgreSQL migration 0009 验证器同步

## 已确认上下文

- 迁移 0009 已在本机测试数据库应用成功，incident suite 已覆盖新 schema。
- `verify-postgresql-roles.sh`、`verify-compose-postgresql-roles.sh`、`verify-postgresql-backup.sh` 中迁移数量硬编码为 8；文档仍以 0008 为当前版本。

## 方案

- 将隔离 role/backup verifier 的预期 migration 数更新为 9；增加 session_generation 与 reactivation_requested_at 列存在性验证，以及 runtime 对所需 users 列的 UPDATE 权限验证。
- 将 Compose 当前栈 verifier 的 ledger 数和权限检查更新到新 schema，维持拒绝 DDL、ledger 和 audit mutation。
- 将当前状态、备份恢复、运维顺序和简历证据同步到 migration 0009；历史阶段报告保留其当时版本语境。

## 文件清单

| 文件 | 操作 | 原因 | 对应 AC |
| --- | --- | --- | --- |
| `examples/openobserve-aiops/verify-postgresql-roles.sh` | modify | 9 迁移及用户列/权限隔离验证 | AC-01 |
| `examples/openobserve-aiops/verify-compose-postgresql-roles.sh` | modify | 当前 Compose ledger 和角色校验更新 | AC-01 |
| `examples/openobserve-aiops/verify-postgresql-backup.sh` | modify | 两种恢复校验 9 迁移与新用户列 | AC-02 |
| `examples/openobserve-aiops/PRODUCTION-READINESS.md` | modify | 当前身份/迁移状态及限制同步 | AC-01–03 |
| `examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md` | modify | 迁移清单及恢复验证说明同步 | AC-02–03 |
| `examples/openobserve-aiops/README.md` | modify | 当前恢复 verifier 文档同步 | AC-02–03 |
| `examples/openobserve-aiops/RESUME-CLAIMS.md` | modify | 增加可复核本机证据，不夸大生产状态 | AC-03 |
| `ROADMAP.md` | modify | 更新 0009 已应用与 verifier 复验结果 | AC-03 |

## 执行顺序

1. 更新三个 verifier 与相关运维/简历文档。
2. 运行无网络 PostgreSQL roles verifier 与隔离明文/加密备份恢复 verifier。
3. 在当前运行的本机 Compose 测试栈运行 role verifier，确认迁移表及 API/worker/migrator 身份。
4. 更新 Roadmap，执行 diff 与范围检查。

## 验证计划

| 层级 | 命令或操作 | 通过条件 |
| --- | --- | --- |
| 隔离角色与迁移 | `bash examples/openobserve-aiops/verify-postgresql-roles.sh` | PG16 无网络 verifier 成功，9 migrations 和权限检查通过 |
| 隔离备份恢复 | `bash examples/openobserve-aiops/verify-postgresql-backup.sh` | 明文及加密恢复含 9 migrations、新用户列和 synthetic incident |
| 本机 Compose 角色 | `source /tmp/holmesgpt-aiops-test-runtime.sh && bash examples/openobserve-aiops/verify-compose-postgresql-roles.sh` | runtime/runtime/migrator 身份分离与 authenticated read 通过 |
| diff | `git diff --check` | 退出码 0 |

## 执行控制

- 状态恢复：on
- 独立审查：off
- 分支策略：当前分支 develop-me
- 提交策略：task，遵守项目 DCO signed-off commit 约束

## 状态机注册

- 已注册 AC：AC-01、AC-02、AC-03
- 已注册任务：T001
- 状态文件：`specs/008-postgres-verifier-0009/state.json`

## 风险与回退

- 这些 verifier 只验证 PostgreSQL 16 本机/隔离数据库，不覆盖任何托管生产数据库。
- verifier 只使用临时隔离卷或当前已授权测试库，不执行 `down -v`、drop database 或清空业务表。
- 脚本出现不兼容时，应修复 verifier/schema 断言，不放宽数据库权限检查。

## 审批

- Plan 状态：approved
- 批准人：monstereat
- 批准时间：2026-09-27
- 批准证据：用户明确授权继续完成 Goal 所需工作和本机数据库操作，无需逐项确认。
