# Spec：本机 Compose PostgreSQL 身份分离

## 目标

让当前本机 Compose 测试运行栈实际以低权限 `aiops_runtime` 账号运行 Incident API 和 worker，并以独立 `aiops_migrator` 账号执行 one-shot migration。`aiops` 管理员账号只供 PostgreSQL 初始化、bootstrap 和运维使用。配置为本机测试专用，不声称已验收托管数据库。

## 非目标

- 不改变 schema、业务数据、用户身份或迁移版本。
- 不更改 PostgreSQL 网络边界、TLS 或托管生产服务。
- 不把本机凭据写入仓库、日志或提交；只将随机本机测试角色密码追加到现有权限为 0600 的临时运行配置。
- 不修改/删除现有数据库卷，也不触碰既存 orphan 容器。

## 验收标准

- **AC-01**：Compose 有可重复的 role bootstrap 门禁；迁移 job 只持有 migrator 凭据，API/worker 只持有 runtime 凭据，三者均不以 `aiops` 管理员身份连接业务数据库。
- **AC-02**：在当前持久化本机测试数据库上，8 个既有 migrations 与业务记录保持不变；runtime 能通过 readiness 并执行真实只读工作台请求，但不能建 schema、修改审计记录或应用 migration。
- **AC-03**：迁移仍由 `aiops_migrator` 成功执行；当前 Compose integration suite 和 PostgreSQL role verifier 通过。
- **AC-04**：PostgreSQL 运维文档和 ROADMAP 准确区分本机测试角色分离与生产托管数据库验收。

## 风险和回退

若新 runtime grant 不足，API/worker 健康检查会失败。回退方式是停止新服务配置并恢复本机 Compose 中原有 `aiops` URL；角色/数据保持不删。Bootstrap 是幂等的，只设定本机角色权限及密码，不执行 DDL migration 或数据修改。

## 待确认

本机 Docker Compose 测试环境内的管理员/运行/迁移角色密码均由隔离配置提供。生产凭据、IAM 映射及托管服务授权仍待平台负责人确定。
