# 实施计划：AIOps admission store capacity baseline

## 已确认上下文

- 相关模块：`examples/openobserve-aiops/alert-trigger/store.py`, `migration_runner.py`, Compose `postgres-test` and `incident-test` services.
- 现有行为：PostgreSQL advisory transaction lock serializes task-cap admission. Current integration tests verify exactly four of 16 requests are admitted at capacity four, but do not retain latency/throughput metrics.
- 不确定项：production platform and workload are not selected, so this is only an isolated local baseline.

## 方案

- CLI reads `AIOPS_TEST_DATABASE_URL` only, validates the exact host/user/database tuple, and requires explicit `--confirm-isolated-test-db`.
- After validation it applies the existing migrations, requires the benchmark tables to have no incident/task/outbox data, runs bounded unique synthetic admissions concurrently, and records admission-store transaction latency.
- It reports JSON to stdout with request counts, accepted/rejected counts, wall throughput, p50/p95/p99, and explicit scope/limitations. It never emits the DSN.
- In `finally`, delete outbox/task/incident rows selected by the benchmark's unique alert-name prefix. Since the strict DB guard and empty-table precondition prevent shared data, this cleanup is limited to the run's synthetic rows.
- No Compose, app runtime, schema, dependency, external service, or production config changes.

## 文件清单

| 文件 | 操作 | 原因 | 对应 AC |
| --- | --- | --- | --- |
| `examples/openobserve-aiops/alert-trigger/benchmark_admission.py` | add | bounded isolated PostgreSQL admission benchmark CLI | AC-01 |
| `examples/openobserve-aiops/alert-trigger/tests/test_capacity_benchmark.py` | add | validate DSN/args/percentiles and report semantics | AC-01 |
| `examples/openobserve-aiops/PRODUCTION-READINESS.md` | modify | document the command and scope limitations | AC-01 |
| `ROADMAP.md` | modify | record actual implementation and verification results | AC-01 |
| `specs/003-admission-capacity-benchmark/**` | add | durable scope and validation evidence | AC-01 |

## 执行顺序

1. Implement and unit-test the database/argument guards and percentile/report generation.
2. Run the CLI in the isolated Compose test profile with bounded synthetic load; verify reported counts and cleanup.
3. Update readiness docs and roadmap with measured local values, explicitly excluding production SLO claims.

## 验证计划

| 层级 | 命令或操作 | 通过条件 |
| --- | --- | --- |
| unit | `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python -m pytest -q tests/test_capacity_benchmark.py` | all unit tests pass |
| isolated benchmark | `docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test python benchmark_admission.py --requests 100 --concurrency 16 --capacity 80 --confirm-isolated-test-db` | JSON reports exactly 80 accepted/20 rejected, finite latency/throughput, and no synthetic rows remain |
| diff | `git diff --check` | exit code 0 |

## 执行控制

- 状态恢复：on
- 独立审查：off (the user explicitly requested direct implementation without review)
- 分支策略：current branch `develop-me`
- 提交策略：task; signed-off commit after verification

## 状态机注册

- 已注册 AC：AC-01
- 已注册任务：T001
- 状态文件：`specs/003-admission-capacity-benchmark/state.json`

## 风险与回退

- Risk: local Docker/PostgreSQL scheduling and disk/CPU make timing noisy and unrepresentative of production.
- Guardrails: no remote DSNs; exact test DB identity; request/concurrency caps; empty-table precondition; run-scoped cleanup.
- 回退：remove the benchmark CLI/tests and revert the documentation/roadmap commit. No migration or persisted user data is changed.

## 审批

- Plan 状态：approved
- 批准人：monstereat
- 批准时间：2026-09-27
- 审批证据：用户在当前 production-readiness goal 中明确授权继续编码及免确认；本 Plan 只触碰隔离测试环境。
