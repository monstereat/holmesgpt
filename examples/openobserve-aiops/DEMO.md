# 本机 AIOps 演示：告警、调查、审批和测试处置

本流程仅操作当前机器上的 Docker 测试环境。先按 [`README.md`](README.md) 启动 Compose 并配置本地账户。浏览器入口默认为订单 demo `http://localhost:8080`、事故工作台 `http://localhost:8081`、OpenObserve `http://localhost:5080`；若设置了 `AIOPS_API_HOST_PORT`，事故工作台和下文发往主机的 curl 请求使用该端口，Compose 内部 webhook 地址仍使用 8081。

`set-chaos-mode` 是本机演示动作，仅在 Incident API 使用 `AIOPS_ENV=local` 时开放。应用会拒绝在其他环境配置 demo 动作开关、owner 地址或 token；不要把它当成生产处置集成。

故障注入状态保存在 `order-action-data` 命名卷中。`CHAOS_MODE` 只在该卷首次初始化时设定初始状态；卷中已有状态时，重启会恢复持久化值，环境变量不会覆盖。后续状态变更应在事故工作台通过审批动作完成。

此文件 journal 是本地单实例、低频演示实现：不要通过 Compose `--scale order-service` 扩成多实例，也不要将其用于生产流量。每个动作会同步重写 journal 快照；记录越多写入越慢，达到 10,000 个 key 后需人工处置才能继续。

## 1. 验证订单遥测

1. 在订单 demo 创建一个正常订单。
2. 在 OpenObserve 查询 `app_logs`，确认订单服务日志和 Trace ID 已写入。
3. 确认浏览器侧错误进入 `frontend_errors`，并与服务端 Trace 使用同一 Trace ID。

## 2. 创建本地故障

1. 在工作台以 `operator` 登录，选中一条事故后申请 `set-chaos-mode` 测试动作，并选择启用。
2. 以独立的 `approver` 账户登录，批准该申请。申请人不能批准自己的动作。
3. 回到 `operator` 账户执行已批准动作。工作台会调用 order-service 的固定内部接口、读取实际状态并记录审计结果。
4. 在订单 demo 创建订单，确认请求失败；在浏览器和 OpenObserve 中用同一 Trace ID 查看错误关联。
5. 使用 `approver` 账户在事故详情填写影响、根因、恢复措施和后续行动，先保存草稿，再标记已审核；`operator` 可查看但不能编辑。复盘内容、审核人和时间会持久化并进入事故审计时间线。

若暂时没有真实 OpenObserve Alert 配置，可在当前 shell 设置同一 webhook token 后发送一个本地测试告警。trace ID 应替换为第 4 步实际观测到的 32 位十六进制 ID：

```bash
curl -fsS "http://127.0.0.1:${AIOPS_API_HOST_PORT:-8081}/webhooks/openobserve" \
  -H "X-Alert-Token: $ALERT_WEBHOOK_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"alert_name":"OrderCreateFailure","trace_id":"0123456789abcdef0123456789abcdef","alert_count":1}'
```

要连接 OpenObserve 原生告警，在 Alert Destination 使用内部地址 `http://incident-api:8081/webhooks/openobserve`，配置 `X-Alert-Token`，并使用字段 `alert_name`、`trace_id`、`alert_count`、`alert_trigger_time_str`。`alert_trigger_time_str` 应包含 `Z` 或显式 UTC 偏移；服务端将其规范化为 UTC。缺失、无效或不带时区的值不会被用于构造事故检索时间窗，Holmes 会按受限近期窗口调查。不要把 token 放进请求体或前端。

## 3. 查看调查状态和证据

工作台的事故详情展示任务状态、Trace ID、Holmes 结论、经过验证的 OpenObserve 工具结果及审计事件。只有 Holmes 成功调用允许的 `app_logs` / `frontend_errors` 工具并返回可接受结果时，证据才会标为已验证。

Live 调查需要 `DEEPSEEK_API_KEY` 和内部 `HOLMES_API_KEY`。OpenObserve OSS 没有原生 RBAC；本地 Holmes 通过隔离网络和只开放只读查询的策略代理访问 OpenObserve，不应把这描述成 OpenObserve 专用只读账户。未设置模型密钥时任务会记录安全错误状态；不要用 `evals/run_evals.py --mode mock` 的 fixture 结果替代 live 验收。mock 与 live 的区别及成本确认见 [`evals/README.md`](evals/README.md)。

## 4. 恢复演示服务

用 `operator` 为关闭 chaos mode 再申请一次测试动作，由 `approver` 单独批准，再执行。确认 order-service 状态为 `off`，然后创建一个正常订单。

工作台对测试动作执行前会复核操作者、资源、参数和审批；执行后读取 order-service 实际状态。如果验证失败会尝试恢复执行前状态并留下审计记录。它不对其他容器、主机或真实业务资源执行操作。

## 5. 容器与数据恢复

```bash
docker compose ps
docker compose restart incident-api incident-worker redis
```

PostgreSQL 是事故状态和 outbox 的持久化来源；worker 重启后会恢复可重试任务。隔离的 API/worker/PostgreSQL 测试运行方式以及不覆盖活动库的 `pg_dump`/`pg_restore` 演练见 [`README.md`](README.md)。保留当前 named volumes；不要用 `docker compose down -v` 清理演示数据。

Order-service 将 `set-chaos-mode` 当前状态和幂等 operation journal 原子写入 `order-action-data` 卷。容器重启后会从同一文件恢复；同一 operation ID 和 payload 会返回已保存结果，不同 payload 会冲突。最多保留 10,000 个 operation ID，达到上限后 owner 会拒绝新动作，避免淘汰仍可能被重试的幂等记录。每个新动作同步重写完整 journal；它只适用于单实例、低频的本地演示，不支持多实例共享，也没有自动把未完成 owner 操作与 Incident API 审批记录对账。测试与生产处置仍关闭。

## 演示边界

- Compose、服务状态、审批和测试动作仅用于本机 Docker 测试环境。
- 发布/Runbook 评测上下文是带来源标识的 synthetic fixture；没有实际 Git/CI 集成，也不声称发布数据来自生产。
- 缺少模型或 OpenObserve 只读凭据时，live Holmes 验收仍未完成。
- 正式环境的身份、凭据管理、网络/TLS、SLO、保留策略、备份目标和动作授权须另行设计；本演示不连接正式环境。
