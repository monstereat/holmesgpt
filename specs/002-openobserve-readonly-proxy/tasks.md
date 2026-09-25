# Tasks：OpenObserve 本机只读策略代理

## T008：实现受限代理并隔离 Compose 网络

- 依赖：无（T007 的应用容器已在本机运行）。
- 对应 AC：AC-01 至 AC-04；对原项目目标对应 AC-04。
- 允许改动：`examples/openobserve-aiops/openobserve-proxy/**`、`examples/openobserve-aiops/docker-compose.yaml`、`examples/openobserve-aiops/holmes-config/config.yaml.example`、`examples/openobserve-aiops/README.md`、`ROADMAP.md`、`docs/develop-me-roadmap.md`、`specs/001-resume-aiops/verify.md`、`specs/002-openobserve-readonly-proxy/**`。
- 操作：实现策略代理、单元测试和非 root 容器；将 OpenObserve 查询改由代理转发；以 Docker 网络使 Holmes 无法绕过代理直连 OpenObserve；配置只读客户端凭据分离和 allowlist 流过滤。
- 验证命令：`docker build -t aiops-openobserve-proxy:test examples/openobserve-aiops/openobserve-proxy && docker run --rm aiops-openobserve-proxy:test python -m unittest discover -s tests -v`。
- 集成验证：读取 `/tmp/holmesgpt-aiops-test-runtime.sh` 中已存在的测试变量，不显示变量值；`docker compose ... config --quiet`、`up -d --build`、Holmes Toolset prerequisites、流过滤及隔离 DNS 验证。若 DeepSeek Key 不存在，不发送模型请求，不声称 live RCA 通过。
- 完成定义：上述测试与范围门禁通过；访问控制覆盖允许与拒绝路径；Compose 拓扑阻止 Holmes 绕过代理；ROADMAP 和 verify 如实区分代理通过与 live 模型调查未验证。
