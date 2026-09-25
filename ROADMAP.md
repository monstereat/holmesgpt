# HolmesGPT AIOps 项目路线图

## 当前阶段

**简历级只读事故诊断闭环：提案待确认。** 当前项目可本机演示 OpenObserve + NestJS 订单故障源 + Python 告警接收器；接收器默认用 `/bin/echo` 替身，不能证明 Holmes 已真实调查。

## 已完成并验证

- OpenObserve Toolset 支持有界只读查询、Trace 检索和流范围约束。
- 本地 Compose 编排 OpenObserve、订单服务和告警接收器；浏览器错误、NestJS 日志与 Trace 可用 Trace ID 关联。
- 告警鉴权、进程内去重/任务查询、签名发布事件原型及事故流程内存模型已有定向测试。
- 20 个合成根因证据样本及结构校验已提交；它们尚未用于 Holmes 诊断质量评测。
- 最近本地验收：三个 Compose 服务健康；告警接收器 9 项测试通过；Webhook 返回 202，`/bin/echo` 调度任务到达 completed。

## 当前进行中

- `specs/001-resume-aiops/` 中的项目范围、验收标准和路线图草案等待用户确认；尚未批准 Plan，也未改业务代码。

## 待办

1. 持久化事故/任务状态，并实现幂等、重试、超时和重启恢复。
2. 接入真实 Holmes 只读调查，保存结论对应的 OpenObserve 证据。
3. 展示事故状态、Trace、调查结果与证据的工作台。
4. 将 20 个样本接入可重复评测，区分 mock 与 live 指标。
5. 真实 OpenObserve 权限、Git/CI 发布关联、正式审批执行和演示录像按外部验收条件推进。

## 阻塞与授权门槛

- PostgreSQL schema/migration 需要用户明确授权；当前只在提案中设计，未实施。
- 真实 Holmes/模型验收需要模型凭据、OpenObserve 地址及只读账户/流白名单。
- 生产处置和发布均不在当前授权范围内。

## 详细进度与验证记录

功能清单、已验证细节和外部验收条件见 [`docs/develop-me-roadmap.md`](docs/develop-me-roadmap.md)。
