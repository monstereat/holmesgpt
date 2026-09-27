# PostgreSQL migration 0009 验证器同步

## 背景与目标

- 背景：新用户生命周期 migration 0009 新增两个 users 列，但权限、备份/恢复 verifier 和运维文档仍把 migration 数写死为 8。
- 目标：确保独立 PostgreSQL 权限校验、当前 Compose 数据库身份校验和明文/加密备份恢复校验继续覆盖最新 schema。
- 成功标准：隔离 PostgreSQL verifier 全通过；当前本机 Compose role verifier 全通过；恢复数据库包含 9 条 migration 与新增 users 列；文档明确本机实证及生产界限。

## 范围

- 包含：更新 verifier 中 migration count 和列权限断言；更新当前用户生命周期/备份恢复/路线图说明；运行验证。
- 不包含：更改应用业务逻辑、清除/重置现有数据库数据、连接生产数据库或执行生产部署。

## 用户与行为

### AC-01：权限与迁移 verifier 接受 migration 0009

- 操作：运行 no-network PostgreSQL role verifier 和当前 Compose role verifier。
- 预期结果：验证 9 migrations；runtime 可更新必要 users 字段但无 DDL/ledger/audit mutation 权限；API、worker 和 migrator 身份仍分离。

### AC-02：备份恢复覆盖新的 schema

- 操作：运行隔离 PostgreSQL 明文及 AES-256-GCM 备份恢复 verifier。
- 预期结果：恢复库含 9 migrations、用户 session generation 与 pending-request 字段，以及既有 synthetic incident；完整性/不覆盖检查通过。

### AC-03：当前文档与验证证据一致

- 操作：更新生产准备、数据库运维、README 与路线图。
- 预期结果：只把已运行验证标记完成；明确这些是本机/隔离验证，不代表生产数据库验收。

## 边界与约束

- PostgreSQL verifiers 使用新的临时容器/数据库；Compose verifier 只读核对当前获授权本机测试库，不重置卷。
- Secrets 由本地 0600 运行环境文件或 verifier 临时凭据提供；不得写入日志或仓库。
- 当前 Mac Docker 仍是本机测试环境；生产 target 未选定。

## 审批

- Spec 状态：approved
- 批准人：monstereat
- 批准时间：2026-09-27
- 审批证据：用户要求继续完成现有 Goal，明确授权所有必要操作及本机数据库操作，无需逐项确认。
