# Tasks：Compose PostgreSQL identity separation

## T001：角色身份检查脚本语法

- 依赖：无
- 对应 AC：AC-01
- 允许改动：`examples/openobserve-aiops/docker-compose.yaml`、`examples/openobserve-aiops/bootstrap-postgresql-roles.sh`、`examples/openobserve-aiops/postgresql-local-role-passwords.psql`、`examples/openobserve-aiops/postgresql-roles.psql`、`examples/openobserve-aiops/verify-postgresql-roles.sh`、`examples/openobserve-aiops/verify-compose-postgresql-roles.sh`、`examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md`、`ROADMAP.md`、`specs/004-compose-db-role-separation/**`。
- 操作：在编码开始前注册 Compose 身份分离脚本的语法验证命令。
- 验证命令：`bash -n examples/openobserve-aiops/bootstrap-postgresql-roles.sh examples/openobserve-aiops/verify-postgresql-roles.sh examples/openobserve-aiops/verify-compose-postgresql-roles.sh`
- 范围基线：`specs/004-compose-db-role-separation/scope-baseline-T001.json`
- 范围报告：`specs/004-compose-db-role-separation/scope-report-T001.json`
- 完成定义：role verifier 通过，确认 runtime 可读 ledger、不能写 ledger/audit 或创建 schema。
- 状态：done

## T002：本机幂等角色 bootstrap/finalize

- 依赖：T001
- 对应 AC：AC-01、AC-03
- 允许改动：`examples/openobserve-aiops/bootstrap-postgresql-roles.sh`、`examples/openobserve-aiops/postgresql-local-role-passwords.psql`、`examples/openobserve-aiops/docker-compose.yaml`、`specs/004-compose-db-role-separation/**`。
- 操作：新增不输出 secrets 的幂等本机 role bootstrap，设置独立 runtime/migrator 密码；Compose 增加 migration 前 bootstrap 和迁移后 finalize 服务。
- 验证命令：`bash -n examples/openobserve-aiops/bootstrap-postgresql-roles.sh`
- 范围基线/报告：`specs/004-compose-db-role-separation/scope-baseline-T002.json` / `scope-report-T002.json`
- 完成定义：shell 语法通过，脚本仅运行 psql grant/password 文件且不打印 DSN/密码。
- 状态：done

## T003：Compose 服务切换至分离身份

- 依赖：T002
- 对应 AC：AC-01、AC-02
- 允许改动：`examples/openobserve-aiops/docker-compose.yaml`、`specs/004-compose-db-role-separation/**`。
- 操作：Incident API/worker 只注入 runtime DSN，迁移 job 只注入 migrator DSN；bootstrap/finalize gate 成功后才允许启动应用。
- 验证命令：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test config --quiet`
- 范围基线/报告：`specs/004-compose-db-role-separation/scope-baseline-T003.json` / `scope-report-T003.json`
- 完成定义：Compose config 通过，配置文本中 API/worker 不引用 admin DSN。
- 状态：done

## T004：更新当前本机测试栈

- 依赖：T003
- 对应 AC：AC-02、AC-03
- 允许改动：`examples/openobserve-aiops/docker-compose.yaml`、`specs/004-compose-db-role-separation/**`。
- 操作：为现有私有运行配置生成两条 URL-safe 随机密码并以 0600 保留；重建本机 Compose 服务，复用现有 PostgreSQL volume，仅将 `aiops` 数据库 public schema 及其关系对象 owner 转给 migrator，并保留审计/事故记录；确认启动前无活动任务。
- 验证命令：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build`
- 范围基线/报告：`specs/004-compose-db-role-separation/scope-baseline-T004.json` / `scope-report-T004.json`
- 完成定义：bootstrap、migrator、finalize 成功；API、worker、PostgreSQL、Redis health 正常；8 个迁移仍在。
- 状态：done

## T005：运行态身份/权限验收

- 依赖：T004
- 对应 AC：AC-02、AC-03
- 允许改动：`examples/openobserve-aiops/verify-compose-postgresql-roles.sh`、`specs/004-compose-db-role-separation/**`。
- 操作：新增运行态 verifier，从实际 API runtime 配置检查 runtime login/ledger read/DDL 与审计拒绝，并从 migration service 配置验证 migrator 连接身份。
- 验证命令：`source /tmp/holmesgpt-aiops-test-runtime.sh && bash examples/openobserve-aiops/verify-compose-postgresql-roles.sh`
- 范围基线/报告：`specs/004-compose-db-role-separation/scope-baseline-T005.json` / `scope-report-T005.json`
- 完成定义：API/worker 以 `aiops_runtime` 连接；迁移连接为 `aiops_migrator`；runtime 无 DDL、ledger DML 或 audit mutation 权限；读取 8 个迁移成功。
- 状态：done

## T006：回归、运维文档和路线图

- 依赖：T005
- 对应 AC：AC-02、AC-03、AC-04
- 允许改动：`examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md`、`ROADMAP.md`、`specs/004-compose-db-role-separation/**`。
- 操作：跑完整 incident Compose test profile；记录本机角色分离证据与生产托管库待验收边界；删除 PostgreSQL 运维文档中重复的恢复说明句子。
- 验证命令：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test`
- 范围基线/报告：`specs/004-compose-db-role-separation/scope-baseline-T006.json` / `scope-report-T006.json`
- 完成定义：全量隔离测试通过；文档链接有效、diff check 通过；未宣称生产托管数据库已验证。
- 状态：done
