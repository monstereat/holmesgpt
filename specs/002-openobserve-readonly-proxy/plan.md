# Plan：OpenObserve 本机只读策略代理

- 部署范围：仅本机 Docker 测试 Compose project。
- Commit strategy：task；状态恢复开启；沿用用户要求跳过独立审查、直接按文档要求编码。

## 实现

- `examples/openobserve-aiops/openobserve-proxy/app.py`：Python HTTP 代理；常量路由 allowlist、独立 Basic Auth、固定 organization/upstream、SQLGlot ClickHouse AST 校验、时间窗和响应大小限制；拒绝重定向和非 JSON 响应。
- `examples/openobserve-aiops/openobserve-proxy/requirements.txt`：固定 SQLGlot 版本，避免自制 SQL 解析器。
- `examples/openobserve-aiops/openobserve-proxy/Dockerfile`：固定 digest 的 Python 基础镜像、非 root、无额外系统包；Compose 开启只读根文件系统、移除 Linux capabilities 并禁止提权。
- `examples/openobserve-aiops/openobserve-proxy/tests/test_proxy.py`：认证、路由/方法、SQL AST、allowlist、上下限和上游契约单测。
- `examples/openobserve-aiops/docker-compose.yaml`：为 Holmes/OpenObserve/order-service/proxy 配置独立网络，使 Holmes 不能直连 OpenObserve；proxy 持有上游 root credential 与客户端凭据，Holmes 只持代理凭据。
- `examples/openobserve-aiops/holmes-config/config.yaml.example`：OpenObserve API URL 指向代理服务名。
- `examples/openobserve-aiops/README.md`、`ROADMAP.md`、`docs/develop-me-roadmap.md`、`specs/001-resume-aiops/verify.md`：说明策略边界与真实模型调查的剩余条件。
- `specs/002-openobserve-readonly-proxy/{spec,plan,tasks,verify,state}.md/json`：记录范围、任务及证据。

## 网络与请求策略

- OpenObserve 仅连接 telemetry-writer 网络和 observe-policy 网络；订单服务只连接 telemetry-writer。
- OpenObserve 加入仅供本机 loopback 端口映射的 observe-ui 网络、order-service 使用的 telemetry-write 网络、proxy 上游网络和告警 webhook 网络；Holmes 未加入任何 OpenObserve 网络。Proxy 只连接 observe-policy 与 holmes-observe 网络；Holmes 只连接 holmes-observe、incident-holmes 和独占 model-egress 网络；worker 连接 incident-holmes。不存在共享 OpenObserve 网络的 Holmes 端点。
- Proxy 接受 `GET /api/default/streams` 和 `POST /api/default/_search`；stream listing 只返回 `app_logs`、`frontend_errors`。
- Search 必须是一条 ClickHouse `SELECT` AST，只有一个 FROM 表且属于白名单；拒绝 CTE、子查询、JOIN、UNION、注释、表函数、database/catalog/schema 前缀及管理语法。强制最大查询时间窗和结果行数及未来时间上限。
- 上游代理连接仅使用固定 OpenObserve 测试 root 凭据；order-service 另持该账号用于 telemetry 写入。客户端代理凭据独立于上游凭据；其他路径、方法、Host、header 均不透传。上游错误响应不向 Holmes 暴露。

## 验证与回退

- 精确命令：`docker build -t aiops-openobserve-proxy:test examples/openobserve-aiops/openobserve-proxy && docker run --rm aiops-openobserve-proxy:test python -m unittest discover -s tests -v`。
- Compose 命令：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml config --quiet && docker compose -f examples/openobserve-aiops/docker-compose.yaml up -d --build`。
- Runtime 验证：Holmes toolset prerequisites 可经代理列出白名单流；从 Holmes 容器尝试直连 `openobserve:5080` 必须 DNS/网络失败；经代理的拒绝请求不得产生上游请求。
- 回退：回退 Compose 与 Holmes config 至代理前版本并重建 Holmes；不清理、删除或覆盖任何数据库卷。
