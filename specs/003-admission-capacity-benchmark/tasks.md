# 任务清单：AIOps admission store capacity baseline

## T001：隔离 PostgreSQL admission benchmark

- 依赖：无
- 允许文件：`examples/openobserve-aiops/alert-trigger/benchmark_admission.py`、`examples/openobserve-aiops/alert-trigger/tests/test_capacity_benchmark.py`、`examples/openobserve-aiops/PRODUCTION-READINESS.md`、`ROADMAP.md`、`specs/003-admission-capacity-benchmark/**`。
- 操作：实现本地 test DB 唯一允许的有界 CLI，统计 admission store 延迟/吞吐并清理本次合成数据；记录结果边界。
- 对应 AC：AC-01。
- 验证：`docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python -m pytest -q tests/test_capacity_benchmark.py`；`docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python benchmark_admission.py --requests 100 --concurrency 16 --capacity 80 --confirm-isolated-test-db`。
- 范围基线：`specs/003-admission-capacity-benchmark/scope-baseline-T001.json`
- 范围报告：`specs/003-admission-capacity-benchmark/scope-report-T001.json`
- 完成定义：tests pass; benchmark reports exact accepted/rejected counts, finite latency/throughput, source DSN is never printed, and it leaves no synthetic incidents/tasks/outbox rows.
- 状态：in_progress
- 证据 ID：`T001-unit`、`T001-isolated-benchmark`
