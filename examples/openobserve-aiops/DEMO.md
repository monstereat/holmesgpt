# OpenObserve AIOps Demo：订单 HTTP 500 调查闭环

启动 OpenObserve 和 NestJS 订单服务，在主机上单独运行告警接收器。
演示链路为：浏览器异常及 Trace ID → 后端 Trace/日志 → OpenObserve 告警 →
调查接收器。当前本地已验证数据采集和告警传递；真实 HolmesGPT 调查仍待验证。

## 快速开始

```bash
cd examples/openobserve-aiops

# OpenObserve root 密码只放在当前 shell 环境，不写入仓库或 .env
export ZO_ROOT_USER_PASSWORD="$(openssl rand -hex 24)"

# 启动 OpenObserve 和订单服务（控制台: http://localhost:5080；用户名默认 demo@example.test）
docker compose up -d --build

# 前端 demo 页: http://localhost:8080
open http://localhost:8080
```

## 故障注入演示（回放脚本）

1. 正常模式点「创建订单」→ 成功（release v1.0.0）。
2. 切到坏发布（模拟 500）：

   ```bash
   export RELEASE_VERSION=v1.0.1
   export CHAOS_MODE=on
   docker compose up -d --build order-service
   ```

3. 再点「创建订单」→ HTTP 500；前端 SDK 上报错误 + trace_id 到 `frontend_errors` 流。
4. 可先发送带签名的发布事件（下节），再由 OpenObserve 告警按下方配置将 webhook 发到主机上的
   `http://host.docker.internal:8081/`；告警接收器调用 `holmes ask` 调查，并检索同一时间窗口的发布记录。
   调查结果会输出到运行接收器的终端。
5. 回到正常发布验证恢复：

   ```bash
   export CHAOS_MODE=off
   docker compose up -d --build order-service
   ```

## OpenObserve 侧配置（控制台一次性操作）

当前本机演示实例已创建 `AIOps Demo - Order Service` 仪表盘、错误告警模板、
`holmes_local_demo` Webhook 目标和 `http_500_trace_investigation` SQL 告警。以下步骤用于
新建实例时复现配置。告警已触发并将明确的 `trace_id` 传给本机接收器；本次检查用 `/bin/echo`
替代 Holmes CLI，因此没有声称真实 Holmes 调查已跑通。

本地演示 Compose 对 OpenObserve 开启 `ZO_SKIP_SSRF_CHECKS`，允许私有 Docker 网络
访问主机 webhook；仅供本地演示，生产环境不要设置。

## 本地模拟 CI 发布事件

订单服务提供 `POST /internal/releases`，接受规范化的发布事件并写入 OpenObserve 的 `app_logs`，
用于按告警时间关联服务、版本、commit 和变更文件。请求须使用运行时环境变量
`RELEASE_WEBHOOK_SECRET` 对原始 JSON 请求体计算 HMAC-SHA256；未配置密钥时端点返回 503，
签名错误返回 401。示例事件会忽略未列入白名单的额外字段，不接收凭据、操作者邮箱等元数据。

以下命令生成临时本地密钥并发送一条演示发布事件；在同一个终端会话中依次运行，不要把真实密钥写入仓库：

```bash
export RELEASE_WEBHOOK_SECRET="$(openssl rand -hex 32)"
docker compose up -d --build order-service

deployed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
payload="$(printf '{"service":"order-service","version":"v1.0.1","commit_sha":"abcdef0123456789","changed_files":["src/orders.ts","src/inventory.ts"],"deployed_at":"%s"}' "$deployed_at")"
signature="$(printf '%s' "$payload" | openssl dgst -sha256 -hmac "$RELEASE_WEBHOOK_SECRET" | awk '{print $2}')"
curl -X POST http://localhost:8080/internal/releases \
  -H 'Content-Type: application/json' \
  -H "X-Release-Signature: sha256=$signature" \
  --data "$payload"
```

成功响应表示演示服务已接收该事件；日志仍经过当前进程内的批量缓冲，不能视为持久队列或生产 webhook。真实 Git/CI 需配置对应的安全凭据并映射为上述字段；当前仓库没有配置或连接任何 GitHub/GitLab/Jenkins 发布流水线。

1. 在 **Management → Templates** 新建 Webhook 模板：

   ```json
   {"alert_name":"{alert_name}","trace_id":"{trace_id}","err_count":"{alert_count}","alert_trigger_time_str":"{alert_trigger_time_str}"}
   ```

2. 在 **Management → Alert Destinations** 新建 Webhook：
   - URL：`http://host.docker.internal:8081/`
   - Header：`X-Alert-Token`，值与运行告警接收器时的 `ALERT_WEBHOOK_TOKEN` 相同。
   - 选择上一步的模板。
3. 在 `app_logs` 流创建 SQL 告警：
   - 查询：`SELECT trace_id, COUNT(*) AS err_count FROM "app_logs" WHERE level = 'error' AND trace_id != '' GROUP BY trace_id`
   - SQL 输出包含 `trace_id` 和 `err_count`；模板用 `{trace_id}` 传递 Trace ID，`{alert_count}` 表示告警查询命中行数。
   - 频率 1 分钟；当查询结果行数大于 0 时触发；目的地选择刚创建的 Webhook。
4. 创建一个包含 `app_logs` 错误数量时间线的仪表盘，用 1 分钟时间桶查看故障注入结果。

## 在主机运行告警接收器

确保本机 HolmesGPT 已安装，`~/.holmes/config.yaml` 已配置下方 OpenObserve 工具集，
且 `holmes` 命令能使用只读账户访问 `localhost:5080`。另开终端运行：

```bash
ALERT_WEBHOOK_TOKEN='本地生成的随机值' poetry run python examples/openobserve-aiops/alert-trigger/trigger.py
```

OpenObserve Webhook Destination 和接收器必须使用同一个本地随机 token；不要将其写入仓库或 `.env`。
接收器拒绝缺少/错误 token、
无效 JSON 和未显式放在 `trace_id` 字段的值；摘要只保留数字计数与合法 ISO 时间，并把告警名/元数据视为不可信输入；
同一告警 5 分钟内去重，最多并发启动 2 次调查。
任务状态只保存在进程内，进程重启会丢失；这不是生产任务队列或事故中心。

## HolmesGPT 侧配置

本机 `~/.holmes/config.yaml`：

```yaml
toolsets:
  openobserve:
    enabled: true
    config:
      api_url: "http://localhost:5080"   # Demo root 账户；生产使用独立只读服务账户
      organization: "default"
      username: "demo@example.test"
      password: "{{ env.OPENOBSERVE_SERVICE_TOKEN }}"
      allowed_streams: ["app_logs", "frontend_errors"]
      max_rows: 100
```

`alert-trigger` 会以如下形式调用：`holmes ask "<告警上下文 + trace_id 调查指令>"`。

## 数据流与字段规范

| 流 | 写入方 | 关键字段 |
| --- | --- | --- |
| `app_logs` | order-service（批量 `_json`） | `trace_id`, `span_id`, `level`, `message`, `service`, `release`, `route` |
| `frontend_errors` | order-service 代前端写入 | `trace_id`, `message`, `stack`, `route`, `user_agent` |
| traces 索引 | OTel OTLP HTTP | W3C 标准（service.name=order-service） |

- Trace 串联：前端 SDK 生成 32 位 trace_id 注入 `traceparent`，NestJS 自动埋点延续同一 trace；
  日志批量写入时从 active span 取 `trace_id`，三类数据可在同一 trace 下对齐。
- 凭据安全：浏览器零凭据；ingest 与查询账户分离；生产要求见
  `holmes/plugins/toolsets/openobserve/SECURITY.md`。
- 浏览器 SDK 会过滤凭据样式字段、邮箱和 URL 查询参数，并支持采样；Demo 采样率为 100%。
  生产使用前仍需按组织的数据治理规范审查脱敏规则并配置采样率。
- 当前告警接收器是内存态 Demo，不保存完整任务状态，也不具备生产级队列、事故审批或处置能力。

## 本地构建（不用 Docker）

```bash
cd demo/order-service
npm install
npm run build
OPENOBSERVE_URL=http://localhost:5080 node dist/main.js
```

## 状态

- [x] demo 服务、极简 SDK、告警触发器、docker-compose 提交
- [x] 本机 OpenObserve 验证（订单日志、前端错误、Trace 同一 trace_id；SQL 告警触发并传递 Trace ID）
- [x] 本地浏览器实际运行 SDK，HTTP 500 后订单日志、前端错误和 Trace 命中同一 trace_id
- [x] 本地规范化发布 webhook 原型：HMAC-SHA256 校验，限量字段写入 `app_logs`，调查提示要求在告警时间附近查找发布事件；CI 平台真实接入仍待配置
- [ ] 生产 OpenObserve 凭据/流权限验证
- [ ] 使用真实 Holmes CLI 完成告警调查；当前已用 `/bin/echo` 验证调度路径
- [ ] 调查录像（asciinema/视频）
