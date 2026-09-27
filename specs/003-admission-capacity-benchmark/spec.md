# AIOps admission store capacity baseline

## 背景与目标

- 背景：Incident API requires an operator-selected pending-task capacity outside local mode. Existing PostgreSQL tests prove admission is atomic under contention, but they do not measure latency or throughput.
- 目标：provide a repeatable, bounded benchmark for the PostgreSQL admission store using only the isolated Compose test database.
- 成功标准：the command refuses any database except the dedicated `aiops_test` database on the Compose-only `postgres` alias; emits machine-readable request counts, throughput, and latency percentiles; removes only rows created by its run; clearly labels results as a local storage baseline, not an API/worker or production SLO.

## 范围

- 包含：a bounded CLI runner, unit tests for input/database guards and metrics, usage documentation, roadmap and verification evidence.
- 不包含：remote/staging/production load traffic, changing production capacity values, HTTP/Celery/Holmes benchmarking, schema changes, dependencies, CI/CD changes, or production deployment.

## 用户与行为

### AC-01：只允许隔离测试数据库并提供 admission 指标

- 前置条件：Compose test profile exposes its temporary PostgreSQL service as hostname `postgres`, database `aiops_test`, user `aiops`.
- 操作：run the CLI with explicit request count, concurrency, capacity, and `--confirm-isolated-test-db`.
- 预期结果：other hosts/users/databases and missing confirmation fail before connecting; the isolated run applies existing migrations, runs unique synthetic admissions, reports accepted/rejected counts and p50/p95/p99 latency plus throughput, cleans only its own synthetic rows, and emits no connection string or secret. Report metadata says the measurement covers the PostgreSQL store only and is not a production SLO.
- 验证方式：unit tests for URL/argument guards and percentile calculations; run a bounded benchmark against the disposable Compose test database and inspect JSON output and cleanup.

## 边界与约束

- Database scope is restricted to the isolated Compose test network and exact `postgres` / `aiops` / `aiops_test` identity tuple.
- Never accept a user-provided DSN or remote host; read only `AIOPS_TEST_DATABASE_URL`.
- Put a hard upper bound on requests and worker concurrency; require explicit operator confirmation flag.
- The benchmark uses synthetic data and deletes only its own rows. Its values do not select a production queue cap or prove an end-to-end service SLO.
- Add no dependency and preserve existing API/runtime behavior.

## 待确认项与假设

- Production platform and load-test target remain unselected; this local benchmark cannot satisfy staging or production capacity acceptance.
- The isolated Compose test network keeps the `postgres` service alias scoped to a tmpfs-only PostgreSQL instance.

## 审批

- Spec 状态：approved
- 批准人：monstereat
- 批准时间：2026-09-27
- 审批证据：用户要求继续当前 production-readiness goal，并明确“任何操作你都有权限，不需要我进行任何确认”；本切片限定为本机隔离测试数据库，不向生产/远程环境发流量。
