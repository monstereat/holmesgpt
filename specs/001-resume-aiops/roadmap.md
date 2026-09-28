# HolmesGPT AIOps 项目路线图

## 目标与阶段

先把当前项目做成可在本机 Docker 测试环境运行、恢复和验收的事故诊断与受控处置闭环；之后再根据明确的生产平台、身份和动作授权要求单独准备正式环境。

当前阶段：**本机 Docker 测试栈已部署；真实模型 RCA 和完整浏览器手工验收待完成。** 用户要求跳过审核直接按文档编码，并授权仅在本机测试 Postgres 应用 schema/migration。未授权正式环境发布、正式数据迁移或真实生产操作。

## 技术边界

- HolmesGPT API、Agent 与工具集继续使用 Python/FastAPI，在现有 `server.py` 边界扩展；不另建 NestJS 控制面。
- NestJS order-service 保留为本地故障源和唯一的测试动作资源。
- OpenObserve 存放日志/Trace；Holmes 通过本机策略代理访问固定 allowlist 流。OpenObserve OSS 无原生 RBAC；策略代理收窄 Holmes 访问路径，不提供租户/用户级数据隔离。
- 调查只读；本地处置仅通过隔离执行器作用于演示订单服务，禁止 Docker socket、任意 Shell 和真实生产凭据。
- 测试报告区分 mock 与 live。具备配置或测试样例不能算作真实外部集成通过。

## 切片与验收

### R01：告警、身份、事故持久化与可靠任务

- 状态：**已完成并通过本机测试**。
- 范围：告警认证和校验、稳定幂等、测试身份/RBAC、事故与任务持久化、可靠异步处理、有界重试、超时、失败终态和重启恢复。
- 验收：重复告警复用事故/任务；未授权请求不排队；API/worker 重启后状态恢复；暂时失败按上限重试。
- 门槛：schema/migration 仅应用于用户授权的本机 Docker 测试库。

### R02：Holmes 只读调查和可核验证据

- 状态：**代理边界已实现；本机 DeepSeek live 合成调查已有证据检索结果；RCA 质量仍未评分**。
- 范围：调用现有 Holmes 调查能力，限制 OpenObserve 只读查询范围，保存结论、时间窗口、Trace 和证据来源；无证据结论标作假设。
- 验收：代理安全测试、本机 Holmes→OpenObserve 合成 live 调查和 20 案 exact evidence 检索结果已有路线图证据。RCA 质量必须经独立人工盲评与分歧裁决；已有 live 合成检索结果不证明诊断准确率，也不替代当前源码恢复测试或生产验收。

### R03：本机事故工作台和处理闭环

- 状态：**核心界面与 API 已实现；状态/严重级别/指派人/全文筛选和列表 keyset 分页源码已补齐，本地登录支持从配置身份切换并自动填充凭据；事故新增 service resource scope 和 migration 0014，已应用到获授权的本机测试库；工作台显示当前身份服务范围，身份映射管理按全局 admin 角色授权；OIDC 登录事务已有跨副本容量上限；详情审计时间线改为最近 50 条 + 按资源授权的 keyset 分页，并新增 migration 0015 索引（尚未应用到本机测试库）；当前权限、登录上限及时间线代码均未功能验证，PostgreSQL verifier 虽按 migration 文件动态计数但未执行；按用户要求暂停自动化与浏览器验证，完整走查待恢复后记录**。
- 范围：在现有 demo 前端呈现事故、任务状态、重试、Trace、结论、证据、审批、执行结果、时间线和复盘；approver/admin 能持久化复盘草稿和审核状态。
- 验收：浏览器从演示故障追到告警、任务和可核验证据；授权操作清楚受角色限制。
- 新增 service scope 约定：Alertmanager 使用 alert `service` label，OpenObserve 使用顶层 `service`，小写服务标识映射 OIDC `resource_scopes`；生产 webhook 缺少标识时拒绝，本机 local mode 才回退 `order-service`。服务 scope 不代表 tenant isolation。Migration `0014_incident_resource_scope.sql` 已应用到本机测试库，对应 API/worker 已重建并更新，但功能与 verifier 尚未验证。

### R04：隔离测试处置、执行验证和恢复

- 状态：**已实现并通过本机审批/执行/恢复验证**。
- 范围：只对 Compose 演示 order-service 开放少量白名单测试动作；执行前重验身份、角色、资源、审批、参数和幂等；执行后验证，失败可回退并审计。
- 验收：未授权/未审批/越权动作零副作用；重复请求幂等；验证失败触发已定义的测试回退。
- 安全边界：不挂 Docker socket，不允许任意命令，不连接真实生产资源。

### R05：发布/Runbook 上下文与 20 例评测

- 状态：**20 例 mock/live 检索评测材料已有；盲评/比较 CLI 已有，保留分歧与输入指纹的第三方裁决汇总入口已编码但未验证；独立人工 RCA 盲评和评分仍待完成**。
- 范围：回放发布事件和 Runbook fixture；把 20 个已知根因样本接入评测，生成机器可读报告。
- 验收：事故/任务/Trace/来源可互查；报告区分合成、mock、live；检索覆盖有机器报告，诊断评分必须由两名独立评审盲评并裁决，不以检索命中率替代 RCA 质量。

### R06：本机 Compose 运维及正式环境迁移准备

- 状态：**本机 Compose、恢复演练和平台无关的生产准备文档已完成；并新增逐服务流量准入矩阵。Alertmanager receiver/template（含 per-alert 与 common annotation 解析、100 条显式上限）、synthetic v4 payload 和独立 staging 验收清单，HTTPS 私网 Prometheus scrape 示例、流式 webhook body 上限及 webhook 4xx/5xx、OIDC 容量拒绝告警已写入源码但暂停验证；OIDC 登录事务生产上限需 owner 通过 `AIOPS_OIDC_MAX_PENDING_LOGINS` 配置，本地 OIDC 默认值为 500；事故审计时间线 keyset 分页及 migration 0015 源码已增加但迁移未应用、功能未验证；生产目标待后续确认**。
- 范围：本机启动、健康检查、数据卷、运行时配置、数据备份/恢复和演示流程；文档列明正式迁移前待确认的域名/TLS、密钥管理、身份、网络、容量/SLO、保留合规、CI/CD 与外部集成。
- 验收：本机测试环境可重复启动、恢复和演示；文档不声称正式集成已实现或验证。
- 禁止范围：本目标不部署正式环境、不迁移正式数据、不执行真实生产动作。

## 待后续确认

- 正式部署平台和身份提供方、租户/RBAC 模型、SLO、数据保留与合规要求。
- 正式环境逐项允许的处置动作、动作拥有方授权接口、审批角色和回退方案。
- Git/CI、Runbook、Alertmanager 实例和是否接 Keep 的目标环境验收。
- 当前 latest live report 的两份盲评打分与分歧裁决；RCA 诊断质量保持 `not_scored` 直到完成。
- 正式环境如需原生 OpenObserve RBAC，需选择支持 RBAC 的发行版；当前本机 OSS 使用网络隔离的策略代理。

## 依赖关系

`R01 → R02 → R03`；`R04` 依赖本地隔离执行器和批准边界；`R05` 依赖 R02；`R06` 包含本机运维与正式迁移准备，但正式发布本身不在当前目标内。
