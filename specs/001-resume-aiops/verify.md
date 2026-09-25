# T000–T009 本机验收记录

日期：2026-09-25
目标环境：当前电脑 Docker Compose 测试环境
整体状态：**未完成 live 验收**。本机业务栈和隔离测试通过；Holmes 的真实模型调查仍缺少外部凭据。

| 验收项 | 结果 | 证据与限制 |
| --- | --- | --- |
| AC-01 告警安全接收和幂等 | 通过 | 事故服务测试通过；本机 HTTP Webhook 创建持久事故/任务，告警鉴权和事务写入由 T001/T002 定向测试覆盖。 |
| AC-02 跨重启恢复 | 通过 | API、worker、Redis、PostgreSQL 重启后服务恢复；已创建 incident 仍存在；outbox/租约恢复由 T002 测试覆盖。 |
| AC-03 有界重试和明确终态 | 通过（mock/故障路径） | Holmes 客户端/worker 的瞬时错误、超时、永久失败与脱敏由定向测试覆盖；本机缺外部配置时任务终态为 `failed:holmes_unavailable`。 |
| AC-04 Holmes 只读调查和证据 | **部分完成，live RCA 阻塞** | T008 增加服务端受限代理和网络隔离；Holmes 无法解析 OpenObserve 服务名，只能访问代理。真实 OpenObserve Toolset 通过代理发现 2 条 allowlist 流并查询到实际日志。Holmes 默认模型为 `deepseek/deepseek-flash`，LiteLLM 支持工具调用；尚未提供 `DEEPSEEK_API_KEY`，未产生模型诊断证据。OpenObserve OSS 仍无原生 RBAC。 |
| AC-05 事故工作台 | 部分验证 | 本机浏览器已加载登录页且无 console error；incident API 登录、事故/任务/审批/复盘接口由隔离集成测试覆盖，工作台脚本静态资源与复盘表单通过检查。本轮未用本机测试账号登录浏览器，未逐项手工走查完整事故/审批页面，因此不记为完整浏览器验收。入口 `http://localhost:8081/`。 |
| AC-06 测试身份、角色、资源授权 | 通过 | operator 与 approver 分开；operator 自审批实测返回 HTTP 403；认证/权限矩阵测试通过。 |
| AC-07 仅限 demo 的受控动作 | 通过（本机演示动作） | operator 请求动作、approver 批准后 order-service 状态 ON；获批恢复后状态 OFF。动作 owner 验证授权、固定资源和幂等键；测试涵盖拒绝、验证和回退路径。 |
| AC-08 本机部署、恢复和重复演示 | 通过（测试环境） | Holmes、incident API/worker、PostgreSQL、Redis、OpenObserve、order-service 在同一 Compose project 运行；健康端点均返回 200。隔离 `aiops_restore_test` 的 pg_dump/pg_restore 成功，活动数据库与 volume 未覆盖/删除。 |
| AC-09 发布和知识上下文关联 | 通过（fixture/mock） | 三条本地发布/Runbook fixture 有来源标记并关联已知根因案例；真实 Git/CI 发布源仍未接入。 |
| AC-10 20 例可重复评测 | 通过（mock 数据产物，不代表诊断质量） | 20 例 mock JSON 报告和结构/关联测试通过；mock 不调用 Holmes/OpenObserve，诊断为空且 `not_scored`。Live 评测需凭据和显式确认，未运行。 |
| AC-11 正式环境边界 | 通过（文档） | README、DEMO 和路线图明确列出迁移前待确认项；未配置或声称已完成正式部署。 |

## 验证结果

- `docker compose ... --profile test run --build --rm incident-test`：**44 passed, 1 warning**；测试使用临时 Postgres 文件系统和隔离 Docker 网络。
- Compose 栈构建、配置校验和服务恢复成功。最近检查：OpenObserve `/healthz`、Holmes `/healthz`、incident API `/healthz`、订单服务根路径均返回 HTTP 200。
- 本机告警/事故工作流：已创建 1 条 incident；无 Holmes 凭据的任务安全终止，错误码 `holmes_unavailable`。
- 本机权限与动作工作流：operator 自审批返回 403；approver 批准后执行并核验 order-service 状态 ON，再完成批准恢复并核验状态 OFF。
- PostgreSQL 备份恢复到独立数据库成功；恢复库 `incidents` 行数为 1。备份 `/tmp/aiops-test.dump` 和恢复数据库保留供检查。
- Holmes/OpenObserve live RCA 和 live 20 例评测没有通过，不得把本机 API 健康、合成 fixture 或 mock 报告描述为 live 结果。
- DeepSeek 切换验证：Compose 配置检查和 Holmes 镜像重建通过；安全检查渲染配置确认模型为 `deepseek/deepseek-flash` 且 API Key 未设置；仓库容器中的 LiteLLM 元数据确认其支持 function calling。没有发出模型请求，live RCA 仍未验证。
- OpenObserve 策略代理：11 项 unittest 通过；Compose 配置及容器重建成功；代理健康、Holmes/incident API/order-service 健康。Holmes 容器内查询 `openobserve` 主机 DNS 失败（预期隔离）；真实 Holmes OpenObserve Toolset 经代理成功列出 2 个 allowlist 流并执行 5 分钟时间窗搜索，返回实际日志行。OpenObserve host `/healthz` 返回 HTTP 200，浏览器 UI 根路径返回 HTTP 308 重定向。
- 持久复盘：隔离 Compose 测试 profile **42 passed, 1 warning**，覆盖迁移、复盘保存/读取、角色拒绝、审核和审计事件。Docker 测试库已记录 `0003_incident_retrospectives`；本机工作台 HTTP smoke 登录 operator 后读取现存事故复盘成功，operator 写入返回 403；`node --check examples/openobserve-aiops/alert-trigger/public/incidents.js` 与 `/incidents.js` HTTP 资源检查通过。
- 20 例评测语料：去重键和发布/Runbook 关联验证 **3 passed**；mock 报告重新生成 20 条，明确保持 `not_scored`，未运行 live 模型请求。
- Live 评测预检：DeepSeek Key 缺失时 runner 在写入 OpenObserve 前拒绝执行；mock 报告断言 20 条、无模型诊断、`not_scored`。
- 本机评测证据准备：OpenObserve 本轮实际接收 40 条 synthetic fixture 行；Holmes 容器经只读代理按本轮 trace 检索到匹配行。没有模型调用，不代表 RCA 已验证。
- 评测/seeder/Holmes API 契约定向 pytest：**36 passed**；增加断言覆盖结构化 release event 字段、Runbook 文本及来源路径进入 live 调查指令，以及三份 Holmes Skills 可由 loader 扫描。
- 运行中的 Holmes 容器扫描实际挂载目录：发现 3 个项目只读 Skill（库存、数据库 schema/migration、发布回归）；未调用模型。

## 后续解阻条件

在本机运行变量中提供有效 `DEEPSEEK_API_KEY`，触发真实订单 HTTP 500 告警，核对模型工具调用、Trace/日志证据链接和诊断假设；随后按 `examples/openobserve-aiops/evals/README.md` 显式运行 live 评测。当前策略代理只提供 Holmes 入口的只读路由/流边界，OpenObserve OSS 仍没有原生用户/租户 RBAC。此记录未授权正式环境部署、数据迁移或真实生产动作。
