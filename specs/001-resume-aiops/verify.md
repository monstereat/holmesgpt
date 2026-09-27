# T000–T009 本机验收记录

日期：2026-09-27
目标环境：当前电脑 Docker Compose 测试环境
整体状态：**本机 live 调用和检索覆盖已验证，整体验收仍未完成**。最新 2026-09-27 synthetic live run `9d27bceb636747d192b839868afae3c9` 经当前字段策略代理完成 20/20 当前 run/case 证据命中、3/3 发布事件命中、0 个工具错误、0 次未限定成功搜索和 0 个检测到的跨案例命中；报告 schema 1.3 校验通过，SHA-256 为 `50fef9dd604e962363c6b8f8b9bb247f3e50d04020393194d7518a30d91d8377`。诊断质量仍未评分，生产部署未执行。

| 验收项 | 结果 | 证据与限制 |
| --- | --- | --- |
| AC-01 告警安全接收和幂等 | 通过 | 事故服务测试通过；本机 HTTP Webhook 创建持久事故/任务，告警鉴权和事务写入由 T001/T002 定向测试覆盖。 |
| AC-02 跨重启恢复 | 通过 | API、worker、Redis、PostgreSQL 重启后服务恢复；已创建 incident 仍存在；outbox/租约恢复由 T002 测试覆盖。 |
| AC-03 有界重试和明确终态 | 通过（mock/故障路径） | Holmes 客户端/worker 的瞬时错误、超时、永久失败与脱敏由定向测试覆盖；本机缺外部配置时任务终态为 `failed:holmes_unavailable`。 |
| AC-04 Holmes 只读调查和证据 | **部分完成，诊断质量验收未完成** | T008 增加服务端受限代理和网络隔离；Holmes 无法解析 OpenObserve 服务名，只能访问代理。真实 OpenObserve Toolset 通过代理发现 2 条 allowlist 流。修复 SQL AST 对合法 `AND` 条件的误判后，当前 Compose proxy 上的 DeepSeek live run 为 20/20 精确 run/case 证据命中、3/3 发布事件命中、0 个工具错误、0 个未限定成功查询和 0 个跨案例命中。根因评分仍为 `not_scored`，需要人工评分。OpenObserve OSS 仍无原生 RBAC。 |
| AC-05 事故工作台 | 通过（本机手工 UI） | 浏览器使用 operator/approver 登录并查看事故详情、任务、审批和审计；operator 无复盘写入/审核按钮，approver 可编辑并审核。approver 保存的合成复盘明确记载模型调查未执行，未伪造 RCA。 |
| AC-06 测试身份、角色、资源授权 | 通过 | operator 与 approver 分开；operator 自审批实测返回 HTTP 403；认证/权限矩阵测试通过。浏览器实际显示角色隔离的审批/执行/复盘控件。 |
| AC-07 仅限 demo 的受控动作 | 通过（本机浏览器闭环） | 浏览器中 operator 请求动作、approver 批准、operator 执行并由 order-service owner 验证状态 OFF→ON；随后独立批准并执行恢复，验证 ON→OFF。审计时间线包含请求、审批和两条 `action.verified`；自动化测试另覆盖拒绝、验证失败和回退路径。 |
| AC-08 本机部署、恢复和重复演示 | 通过（测试环境） | Holmes、incident API/worker、PostgreSQL、Redis、OpenObserve、order-service 在同一 Compose project 运行；健康端点均返回 200。隔离 `aiops_restore_test` 的 pg_dump/pg_restore 成功，活动数据库与 volume 未覆盖/删除。 |
| AC-09 发布和知识上下文关联 | **本机端到端通过；真实 CI 接入待完成** | `verify-release-event-e2e.sh` 用临时签名密钥调用发送脚本，经 NestJS 验签和 OpenObserve 入库后，以精确版本和 commit 查询到唯一 `release_deployed` 事件。可复用 GitHub workflow 已提供，但仍无部署 caller、远端 secret binding 或 staging 事件验收。 |
| AC-10 20 例可重复评测 | **部分通过（诊断准确性未评分）** | 最新 live 报告 schema 1.3 通过 Draft 2020-12 校验；本轮 40 条 synthetic 行在 Holmes 调用前确认可搜索；20 案完成，20/20 exact run/case 证据命中、3/3 release event 命中，未限定成功查询、跨案例命中和工具错误均为 0。Diagnosis scoring 仍为 `not_scored`，不能代表诊断准确率。报告 [`live-2026-09-27.json`](../../examples/openobserve-aiops/evals/reports/live-2026-09-27.json) SHA-256：`50fef9dd604e962363c6b8f8b9bb247f3e50d04020393194d7518a30d91d8377`。 |
| AC-11 正式环境边界 | 部分通过（准备文档；生产部署未执行） | 用户明确当前 macOS Docker Desktop 24.0.6 / Compose 2.23.0 仅是本地测试环境，正式生产平台尚未选择。身份、域名/TLS、数据服务身份、OpenObserve RBAC/租户、告警路由、SLO/RPO/RTO、动作责任人等仍待确认，尚无完整目标专属生产 manifest 或生产凭据。已增加 provider-neutral OTel Collector 生产配置模板和 PostgreSQL 运维手册，但模板未替代平台部署资产。托管库 IAM 兼容、加密异地备份、PITR 和实测 RPO/RTO 仍未验收。 |

## 验证结果

- 2026-09-27 T007 完整 Incident API/workbench 隔离 Compose suite：`source /tmp/holmesgpt-aiops-test-runtime.sh && docker compose -f examples/openobserve-aiops/docker-compose.yaml --profile test run --rm incident-test`，**105 passed，1 个 Starlette/AnyIO 上游弃用 warning，21.52s**。测试 PostgreSQL 使用无持久卷隔离容器，测试后停止；本机 API `/readyz` 返回 `ready`，Holmes、API、worker、Redis、PostgreSQL、proxy 与 order-service 均 healthy。发现的既存 orphan `alert-trigger` 未清理。生产目标尚未选择，T007 整体验收仍未完成。
- 2026-09-27 发布事件端到端复验：`bash -n examples/openobserve-aiops/verify-release-event-e2e.sh` 与脚本实际运行通过；一次临时订单服务副本只绑定 loopback 随机端口，使用随机测试 secret 接收发布脚本签名事件，显式 flush 后在现有本机 OpenObserve 以唯一 version+commit 精确检索到 1 条 `release_deployed`。临时副本退出，现有 Compose 服务仍健康。此为本机 synthetic integration，不代表 GitHub workflow、staging 或生产发布已接通。
- 2026-09-27 PostgreSQL 上线操作边界：新增 [`POSTGRESQL-OPERATIONS.md`](../../examples/openobserve-aiops/POSTGRESQL-OPERATIONS.md)，将已验证的 migration `0001`–`0008`、角色权限与隔离恢复证据和未验证的 provider IAM、加密异地保留、PITR、staging rollback/PITR 演练分开说明。变更通过 `git diff --check`，文档链接目标存在；未执行生产数据库操作。
- 2026-09-27 OTel Collector 生产配置：新增独立的 TLS/Bearer-token 接收和 HTTPS/文件式 upstream auth、带界限的磁盘发送队列模板；`bash -n examples/openobserve-aiops/verify-production-otel-config.sh && bash examples/openobserve-aiops/verify-production-otel-config.sh` 通过固定 digest 的 Collector `validate`，临时容器使用 `--network none`，未连接服务端。真实证书、令牌轮换、加密持久卷、私网边界、File Storage beta 支持状态接受度及容量需在目标 staging 环境验收。
- 2026-09-27 人工 RCA 评审工具：`review_scoring.py compare` 要求两个不同 reviewer ID、相同报告 SHA/run ID 和相同完整 20 案；逐维度计算 exact agreement 与 mean absolute difference，列出 unsafe-remediation 分歧和待裁决病例，不平均分数。发现旧评审表把参考答案与模型结果放在一起，已改为不含参考字段的盲评表和独立 mode-0600 答案 key，summary 递归拒绝含参考答案的评审表，prepare 拒绝覆盖既有评审材料。Docker 隔离定向测试 **7 passed**；已从 live 报告生成 reviewer A/B 两份 20 案空白表及单独 key，评审表无参考字段，文件权限均为 `0600`。独立人工评分与分歧裁决仍未完成，诊断分数仍为 `not_scored`。
- 2026-09-27 复跑 PostgreSQL 16.6 隔离备份恢复 verifier 与 runtime-role verifier，二者均退出码 0。恢复验证覆盖 migrations 0001–0008、任务耗时索引、incident severity/assignee 和 synthetic incident，并检查备份权限/覆盖拒绝；角色 verifier 确认 runtime 可追加/读取 audit 并更新 triage 列，但不能修改审计或创建 schema。临时 verifier 使用无网络、无持久卷容器，没有接触本机业务 PostgreSQL volume。
- 历史隔离 Compose suite（当时 schema 1.2 代码）：**46 passed, 1 warning**；该数字不是当前树的全量回归结果。
- 历史全仓非 LLM 回归：**3861 passed, 160 skipped, 0 failed, 119 warnings**；Internet Toolset 在应用层禁用环境代理和 `.netrc`，并保留 `REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE`，SSRF pinning 与代理环境回归测试通过。这是历史全仓结果，不代表本轮重新执行。
- Compose 栈构建、配置校验和服务恢复成功。最近检查：OpenObserve `/healthz`、Holmes `/healthz`、incident API `/healthz`、订单服务根路径均返回 HTTP 200。
- 本机告警/事故工作流：已创建 1 条 incident；无 Holmes 凭据的任务安全终止，错误码 `holmes_unavailable`。
- 本机权限与动作工作流：operator 自审批返回 403；approver 批准后执行并核验 order-service 状态 ON，再完成批准恢复并核验状态 OFF。
- PostgreSQL 备份恢复到独立数据库成功；恢复库 `incidents` 行数为 1。备份 `/tmp/aiops-test.dump` 和恢复数据库保留供检查。
- Holmes/OpenObserve live 合成调查与 20 案 live 评测已实际运行，检索覆盖复核通过；不得把 API 健康、合成 fixture 或 `not_scored` 报告描述为诊断准确率或生产验证。
- DeepSeek live 验证：已使用私有本机配置实际运行 20 案 synthetic live evaluation；该结果证明模型调用、工具检索和来源追踪链路运行，但不证明 production 模型质量。凭据值未写入报告或仓库。
- 2026-09-27 当前 SQL 字段策略代理复验：本机 DeepSeek live run `9d27bceb636747d192b839868afae3c9` 完成，20/20 exact run/case 证据命中、3/3 发布事件命中、0 个工具错误、0 次未限定搜索和 0 个跨案例命中；报告 schema 1.3 通过 Draft 2020-12 校验。报告已保存在仓库 `examples/openobserve-aiops/evals/reports/live-2026-09-27.json`。先前生成的 v1 reviewer sheet 包含参考答案，不能用于独立盲评；现已生成 v2 盲评表和单独 key（详见上述评分工具复验记录）。独立人工评分与裁决仍未完成，诊断仍为 `not_scored`。
- 2026-09-26 当前事故服务完整隔离 Compose suite：**103 passed, 1 Starlette/AnyIO deprecation warning**；临时 PostgreSQL 集成测试已启用。Compose 提醒存在既存 orphan `alert-trigger` 容器，本次没有删除它，测试进程退出码为 0。
- 浏览器端到端手工验收：operator/approver 分别登录；operator 申请 `set-chaos-mode:on`，approver 批准，operator 执行并观察 owner 验证 OFF→ON；再经审批执行 OFF 恢复并验证 ON→OFF。operator 无复盘写入/审核按钮，approver 保存并审核含真实限制说明的合成复盘；审计时间线显示审批、动作核验和 `retrospective.reviewed`。合成事故的 Holmes 任务以 `holmes_unavailable` 到达重试上限，证明任务失败路径可见但不能替代 live RCA。
- 正式环境准备：`examples/openobserve-aiops/PRODUCTION-READINESS.md` 列出服务职责、待确认部署输入、生产配置门槛、staging 分阶段验收和 go/no-go 证据。它是准备文档，不是可执行生产 manifest；未执行生产部署。
- 最新 mock 重放重新生成 20 条报告，并在项目 Poetry Python 3.12 环境通过 Draft 2020-12 Schema 校验；全部案例 `diagnosis: null`、`scoring: not_scored`。它验证了产物结构和可重复生成，不代表 Holmes 诊断质量。
- OpenObserve 策略代理：11 项 unittest 通过；Compose 配置及容器重建成功；代理健康、Holmes/incident API/order-service 健康。Holmes 容器内查询 `openobserve` 主机 DNS 失败（预期隔离）；真实 Holmes OpenObserve Toolset 经代理成功列出 2 个 allowlist 流并执行 5 分钟时间窗搜索，返回实际日志行。OpenObserve host `/healthz` 返回 HTTP 200，浏览器 UI 根路径返回 HTTP 308 重定向。
- 持久复盘：隔离 Compose 测试 profile **42 passed, 1 warning**，覆盖迁移、复盘保存/读取、角色拒绝、审核和审计事件。Docker 测试库已记录 `0003_incident_retrospectives`；本机工作台 HTTP smoke 登录 operator 后读取现存事故复盘成功，operator 写入返回 403；`node --check examples/openobserve-aiops/alert-trigger/public/incidents.js` 与 `/incidents.js` HTTP 资源检查通过。
- 20 例评测语料：去重键和发布/Runbook 关联验证 **3 passed**；mock 报告重新生成 20 条，明确保持 `not_scored`。该 mock 重放本身不运行 live 模型请求；live synthetic 评测结果见 AC-10 和上方记录。
- Live 评测预检：DeepSeek Key 缺失时 runner 在写入 OpenObserve 前拒绝执行；mock 报告断言 20 条、无模型诊断、`not_scored`。
- 本机评测证据准备：OpenObserve 本轮实际接收 40 条 synthetic fixture 行；Holmes 容器经只读代理按本轮 trace 检索到匹配行。没有模型调用，不代表 RCA 已验证。
- 评测/seeder/Holmes API 契约定向 pytest：**38 passed**；增加断言覆盖结构化 release event 字段、Runbook 文本及来源路径进入 live 调查指令、三份 Holmes Skills 可由 loader 扫描及 exact eval-record 计数。
- 评测 mock 报告 schema 1.2 显式区分 `reference_*` 答案字段，未解析假设使用 `null`；Draft 2020-12 validator 校验通过，11 项评测语料/报告测试通过。报告输出逐案证据/release 检索覆盖，根因准确度继续标为 `not_scored`。
- 运行中的 Holmes 容器扫描实际挂载目录：发现 3 个项目只读 Skill（库存、数据库 schema/migration、发布回归）；未调用模型。

## 后续验收条件

人工建立并执行根因诊断评分 rubric；需要时以当前 run/case SQL matcher 复跑 20 案以复现 20/20 检索覆盖。触发真实订单 HTTP 500 告警，核对模型工具调用、Trace/日志证据链接和诊断假设。当前策略代理只提供 Holmes 入口的只读路由/流边界，OpenObserve OSS 仍没有原生用户/租户 RBAC。此记录未授权正式环境部署、数据迁移或真实生产动作。
