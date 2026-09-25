# OpenObserve 本机只读策略代理

## 目标

在本机 Docker 测试环境，为 OpenObserve OSS 增加服务器侧强制的查询边界，使 Holmes 只能读取明确允许的日志流，并阻止 Holmes 容器绕过代理直连 OpenObserve。

## 范围

- 代理仅公开 Holmes 当前 OpenObserve Toolset 所需的日志流列表和搜索接口。
- 校验独立客户端凭据、固定组织、流白名单、单 SELECT SQL、时间范围、请求/响应大小和行数。
- Compose 网络分离 OpenObserve、订单服务、代理与 Holmes；OpenObserve UI 仍仅绑定本机回环地址。
- 加入代理策略测试、Compose 部署和恢复文档。

## 不在范围

- 正式环境部署、生产数据或生产 OpenObserve 权限配置。
- 通用 SQL 网关、任意 OpenObserve API 代理、写入/管理 API 代理。
- DeepSeek 凭据配置；真实模型调查必须由用户在本机注入 API Key 后另行验证。

## 验收标准

- AC-01：未认证客户端、非允许路由/组织/方法、非法或越权 SQL、超窗搜索均在访问 OpenObserve 前被拒绝。
- AC-02：Holmes 容器网络上无法直接解析或连接 OpenObserve；只有代理能访问 OpenObserve 搜索 API。
- AC-03：流列表仅返回白名单流；请求体与响应均有大小及行数上限；允许请求按固定上游身份访问本机测试 OpenObserve。
- AC-04：Docker Compose 配置、代理镜像和策略测试通过，Holmes 配置经代理健康检查可发现 allowlist 流；全链路模型调查状态如实记录。

## 已知边界

代理将 OpenObserve root credential 保存在代理容器环境中；order-service 也保留同一测试账号用于 telemetry 写入。代理将 Holmes 的上游能力收窄至固定只读 HTTP 路由和流。若代理或其宿主容器本身被攻破，攻击者仍可能滥用该上游身份；这不替代 OpenObserve Enterprise/Cloud 原生 RBAC。
