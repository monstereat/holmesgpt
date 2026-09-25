# HolmesGPT AIOps 项目路线图

## 当前阶段

**本机 Docker 测试环境优先：Spec/Plan 已批准，按用户要求跳过独立 Plan 审查并开始编码。** 用户已明确先部署到当前电脑 Docker 测试环境，后续再上正式环境。本目标不连接/部署正式环境，也不执行真实生产操作。当前项目可本机演示 OpenObserve + NestJS 订单故障源 + Python 告警接收器；接收器默认用 `/bin/echo` 替身，不能证明 Holmes 已真实调查。

## 已完成并验证

- OpenObserve Toolset 支持有界只读查询、Trace 检索和流范围约束。
- 本地 Compose 编排 OpenObserve、订单服务和告警接收器；浏览器错误、NestJS 日志与 Trace 可用 Trace ID 关联。
- 告警鉴权、进程内去重/任务查询、签名发布事件原型及事故流程内存模型已有定向测试。
- 20 个合成根因证据样本及结构校验已提交；它们尚未用于 Holmes 诊断质量评测。
- 最近本地验收：三个 Compose 服务健康；告警接收器 9 项测试通过；Webhook 返回 202，`/bin/echo` 调度任务到达 completed。
- T000：order-service 新增仅限 `set-chaos-mode` 的内部测试动作接口；服务端验证 token、资源、参数和幂等键；4 项定向 Node 测试通过，TypeScript build 通过。

## 当前进行中

- `specs/001-resume-aiops/` 的 Spec 已获用户确认并通过独立审查；Plan/Tasks 已按用户要求直接批准。数据库迁移获授权仅作用于本机 Docker 测试库。
- T001：事故/任务/审批/审计/outbox PostgreSQL schema、稳定指纹与事务写入、带资源范围的测试角色授权、口令哈希与签名会话已完成；一次性本机 PostgreSQL 集成测试验证迁移、重复事件去重及 outbox 失败回滚。
- T002：Webhook 认证和边界校验后事务创建事故/任务/outbox；Celery worker 有原子 claim、租约、退避重试和失败终态，dispatcher 周期重投队列中丢失的任务并回收过期租约；`trigger.py` 不再调用 Holmes CLI 或 `/bin/echo`。隔离 PostgreSQL 全套测试 21 passed；无数据库时 3 passed、4 个数据库集成项安全跳过。Compose 数据卷和 Redis 联调待 T007。
- 当前任务：T003，Holmes/OpenObserve 只读调查客户端。需要模型与 OpenObserve 只读凭据的 live 验收仍待 T007；没有凭据不得标为通过。

## 待办

1. 持久化事故/任务状态，并实现幂等、重试、超时和重启恢复。
2. 接入真实 Holmes 只读调查，保存结论对应的 OpenObserve 证据。
3. 展示事故状态、Trace、调查结果与证据的工作台。
4. 将 20 个样本接入可重复评测，区分 mock 与 live 指标。
5. 实现本机测试角色授权、隔离测试动作、执行验证/回退和本地数据恢复；不得使用 Docker socket、任意 Shell 或真实生产凭据。
6. 将发布/Runbook 测试 fixture 与 20 例样本接入可重复评测；具备外部测试凭据时单独验收 live 集成。
7. 记录正式环境迁移待确认项；正式部署、真实数据迁移和生产处置不属于当前执行范围。

## 阻塞与授权门槛

- 本机测试 PostgreSQL schema/migration 已获用户明确授权；授权不包含正式数据库或任何生产数据。
- 真实 Holmes/模型验收需要模型凭据、OpenObserve 地址及只读账户/流白名单。
- 当前授权的部署目标是本机 Docker 测试环境。正式部署、生产数据迁移或生产处置需后续分别明确授权；“后续会上正式的”不等于当前授权。

## 详细进度与验证记录

功能清单、已验证细节和外部验收条件见 [`docs/develop-me-roadmap.md`](docs/develop-me-roadmap.md)。
