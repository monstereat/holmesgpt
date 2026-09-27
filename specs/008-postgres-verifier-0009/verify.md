# 验证记录：PostgreSQL migration 0009 验证器同步

## 范围与 Diff

- 计划内文件：独立角色 verifier、当前 Compose 角色 verifier、备份/恢复 verifier、生产准备、数据库运维、README、简历证据、Roadmap。
- 实际新增涉改文件：无源码文件新增；受控工作流工件 `specs/008-postgres-verifier-0009/**`。
- 范围门禁：pass。
- 无关改动：两份预先存在的 `specs/001-resume-aiops/state-*.json` 未修改。

## 自动验证

| 验证 | 命令／操作 | 结果 | 证据 |
| --- | --- | --- | --- |
| 隔离 roles verifier | `bash examples/openobserve-aiops/verify-postgresql-roles.sh` | 首次发现 verifier 漏登记 0009（8 条）；修正登记后 pass：9 migrations、runtime users 列权限和 DDL/audit/ledger 拒绝 | `evidence/T001-roles-rerun.stdout.log` |
| 隔离明文/加密备份恢复 | `bash examples/openobserve-aiops/verify-postgresql-backup.sh` | pass；两种恢复均包含 9 migrations、用户生命周期列和 synthetic incident | `evidence/T001-backup.stdout.log` |
| 当前 Compose role verifier | `source /tmp/holmesgpt-aiops-test-runtime.sh && bash examples/openobserve-aiops/verify-compose-postgresql-roles.sh` | pass；runtime API/worker、migrator 身份与 9 migrations 正确 | `evidence/T001-compose-roles.stdout.log` |
| 文档与脚本 whitespace | `git diff --check` | pass | `T001-diff-rerun` |

## 结构化证据

- 状态文件：`specs/008-postgres-verifier-0009/state.json`
- Scope 报告：`scope-report-T001.json`
- 命令证据 ID：`T001-roles-rerun`、`T001-backup`、`T001-compose-roles`、`T001-diff-rerun`
- stdout/stderr：`evidence/`
- Git HEAD：`3e7978ce8dfdc34700376ee9faecff8ee8af907a`
- 源码 diff SHA-256（采集时，不含后续 verify artifact）：`588a31789e996811544b2acad768651aadac8be271f90ed1a545209036000034`

## 验收标准追踪

| AC | 结果 | 验证证据 | 备注 |
| --- | --- | --- | --- |
| AC-01 | pass | `AC-01-role-verifiers` | 两个 role verifier 均过。 |
| AC-02 | pass | `AC-02-backup-restore` | 隔离库明文/加密 restore 均过。 |
| AC-03 | pass | `AC-03-doc-sync` | 文档同步并将本机与生产边界区分。 |

## 例外与风险

- 首次 `verify-postgresql-roles.sh` 失败，因为新 migration 不在该脚本的 ledger 回填列表；补上 0009 后完整重跑成功。失败日志保留于 `evidence/T001-roles.stderr.log`。
- 当前测试卷未清理；incident API/worker/PostgreSQL/Redis healthy，`/readyz` 为 ready，现存 incidents/tasks/audit/outbox 为 11/11/53/11。
- 仅验证本机和隔离 PostgreSQL 16；未验收托管生产 PostgreSQL、异地加密保留、PITR 或实测 RPO/RTO。

## 结论

- 状态：ready_for_review
- 审查人：等待整体 Goal 后续验收
- 人工验收证据：用户授权继续执行 Goal 与本机 PostgreSQL 迁移；未执行生产操作。
