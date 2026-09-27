# Plan：Compose 数据库身份分离

## 方案

使用现存 PostgreSQL 管理账号 `aiops` 作为仅本机 bootstrap 身份；新增幂等的一次性 access setup job，执行已有 role grant 模板并设置两个独立、随机的本机密码。为兼容当前本机持久卷中由旧超级用户创建的 public schema 对象，setup/finalize 仅在 `aiops` 数据库内将 public schema 及该 schema 中的关系对象 owner 转给 `aiops_migrator`；不转移其他数据库/共享对象、不改 DDL 或数据。Compose 启动顺序为 PostgreSQL → access setup → migrator migration job → access finalize → API/worker。finalize 在迁移后重跑权限模板，保证首次建库时 runtime 也无法修改 `schema_migrations`。API/worker URL 改为 `aiops_runtime`，migration URL 改为 `aiops_migrator`。测试 profile 的临时数据库保留现有测试身份，避免把测试 fixture 的建表权限伪装成 runtime 权限。

角色凭据追加到现有 mode-0600 本机临时运行 shell 文件，不改仓库 `.env`，不输出值。秘密只由对应 Compose 服务注入：PostgreSQL/admin bootstrap 持有管理员密码；API/worker持有 runtime 密码；migration job 持有 migrator 密码。日志与命令输出不得打印 DSN 或密码。

## 文件变更

- `examples/openobserve-aiops/docker-compose.yaml`：加 bootstrap/finalize gate，并给 API/worker/migration 使用独立 DSN。
- `examples/openobserve-aiops/bootstrap-postgresql-roles.sh`：幂等执行角色权限与本机 role password SQL。
- `examples/openobserve-aiops/postgresql-local-role-passwords.psql`：只含 psql 变量占位符，为两个本机角色设置独立密码。
- `examples/openobserve-aiops/postgresql-roles.psql`：runtime 不得变更 migration ledger，并保留 audit append-only 权限。
- `examples/openobserve-aiops/verify-postgresql-roles.sh`：验证 runtime 无 migration-ledger 写权限。
- `examples/openobserve-aiops/verify-compose-postgresql-roles.sh`：通过当前运行中的 API、worker、migration 配置验证数据库连接身份、最小权限和现存 8 个迁移。
- `examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md`、`ROADMAP.md`：说明本地结果及托管库验收边界，修复重复的恢复机制说明。
- `specs/004-compose-db-role-separation/**`：本切片状态、验证和范围证据。

## 验证和回退

按连续 Task 注册并运行命令：shell 语法、隔离 PostgreSQL role verifier、Compose 配置、重建并恢复本地服务、实际 Compose 身份/权限 verifier、完整 incident test profile。对当前持久本地卷只应用角色授权，不运行新 DDL migration；启动前确认没有 queued/running/retrying task。若启动或权限 verifier 失败，停止本地应用服务，并在 Compose URL 上回退为旧 `aiops` 身份；不得删除数据卷或容器。

## 提交策略

`phase`：全部本地验证通过后一次带 sign-off 的提交，不推送。
