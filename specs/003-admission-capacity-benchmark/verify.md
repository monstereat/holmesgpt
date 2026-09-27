# 验证记录：本机 PostgreSQL admission 容量基线

日期：2026-09-27
环境：本机 Docker Compose 隔离测试 PostgreSQL（临时容器，无业务持久卷）
结论：AC-01 本地验证通过；不构成生产容量、端到端吞吐或 SLO 结论。

## 实现边界

- `benchmark_admission.py` 只从 `AIOPS_TEST_DATABASE_URL` 读取连接，并严格限制 PostgreSQL 服务别名、用户名、数据库名和端口；CLI 必须给出 `--confirm-isolated-test-db`。
- 请求数最多 5,000、并发最多 64；只接受空的 incidents/tasks/outbox 测试库，成功或失败后仅清理本轮随机前缀数据，并再次验证三张表为空。
- JSON 结果包含准入/拒绝数量、吞吐和 p50/p95/p99 延迟，并显式输出 `production_slo: false` 与测量限制。脚本不输出 DSN。

## 验证结果

1. 隔离 Compose 定向单测：

   ```bash
   docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python -m pytest -q tests/test_capacity_benchmark.py
   ```

   结果：**18 passed in 0.54s**。

2. 本机隔离数据库 100 请求基线：

   ```bash
   docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python benchmark_admission.py --requests 100 --concurrency 16 --capacity 80 --confirm-isolated-test-db
   ```

   | 指标 | 实测 |
   | --- | ---: |
   | 请求数 / 并发 / 容量 | 100 / 16 / 80 |
   | 准入 / 满载拒绝 | 80 / 20 |
   | 总吞吐 | 43.401 请求/秒 |
   | 准入吞吐 | 34.721 请求/秒 |
   | admission 延迟 p50 / p95 / p99 | 332.601 / 518.144 / 563.848 ms |
   | 清理后验证 | 通过，incidents/tasks/outbox 均为空 |

   原始 JSON：[`evidence/T001-isolated-benchmark.stdout.log`](evidence/T001-isolated-benchmark.stdout.log)。结果确认 `scope=isolated-compose-test-postgres-only`、`http_or_worker_path_included=false`、`production_slo=false`。benchmark stderr 只报告既存 orphan 容器提示；`postgres-test` 已停止。

3. 本机业务服务复核：Incident API `/readyz`、Holmes `/readyz`、OpenObserve `/healthz`、order-service 根路径均 HTTP 200；Compose 中 Incident API/worker、PostgreSQL、Redis、proxy 和 Holmes 状态 healthy。既存 orphan `alert-trigger` 未删除。

## 限制与后续

该基线包含客户端数据库连接建立和 PostgreSQL admission transaction；不包括 HTTP、认证、outbox dispatch、Redis/Celery、worker、Holmes、模型或通知。它不能用于选择生产 `AIOPS_MAX_PENDING_TASKS`，也不能作为生产 SLO 或简历中的生产容量指标。目标 staging 环境确定后，仍需执行有代表性的端到端负载、worker 吞吐/积压恢复、告警路由和故障恢复验收。
