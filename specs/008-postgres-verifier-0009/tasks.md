# 任务清单：PostgreSQL migration 0009 验证器同步

## T001：同步 verifier、运维文档与验证证据

- 依赖：无
- 允许文件：三个 PostgreSQL verifier、`PRODUCTION-READINESS.md`、`POSTGRESQL-OPERATIONS.md`、`README.md`、`RESUME-CLAIMS.md`、`ROADMAP.md`
- 操作：覆盖 migration 0009 的权限和备份恢复核验，更新当前文档，并运行隔离和当前栈验证。
- 对应 AC：AC-01、AC-02、AC-03
- 验证：隔离 roles verifier；隔离备份/恢复 verifier；本机 Compose role verifier；`git diff --check`。
- 状态机验证命令：在 `add-task --command` 登记的精确命令。
- 范围基线：`specs/008-postgres-verifier-0009/scope-baseline-T001.json`
- 范围报告：`specs/008-postgres-verifier-0009/scope-report-T001.json`
- 完成定义：三项 verifier 与 diff check 全部通过，文档只写入已测事实和生产边界，scope gate 通过。
- 状态：pending
- 证据 ID：`T001-roles`、`T001-backup`、`T001-compose-roles`、`T001-diff`
