# OIDC 用户受控恢复与会话失效 Spec 独立审查

## P0：阻塞问题

- 无。

## P1：需处理或明确接受的问题

- 初审指出停用无法中断已鉴权的在途请求；Spec 已限定线性化边界为停用事务提交后开始鉴权的新请求，并明确在途请求可能完成。
- 初审指出旧实例不会校验 session generation；Spec 已要求在升级/切流期间禁止恢复用户，全部旧实例退出后才允许恢复。
- 复审指出 callback / 管理恢复状态和审计需要原子化；Spec 已要求事务/条件更新、重复 callback 只建一次申请、并发审批最多一个成功。
- 复审指出恢复审计未分离 actor 与 target 且未禁止自我恢复；Spec 已要求记录 actor_id、target user_id/username，并禁止管理员恢复自身。

## P2：非阻塞优化

- 已在 Spec 增加 401/403/404/409/422 结果码、空请求体约束和重复停用行为。
- 已在 Spec 增加 bearer/cookie、pending 申请时间保留、仅新 OIDC session 可访问及失败请求不写成功审计的验收方向。

## 结论

- 状态：pass
- 审查范围：`specs/007-oidc-user-reactivation/spec.md`
- 审查人：reactivation_spec_review；prod_doc_audit
- 复审状态：所有 P1/P2 均已落实到 Spec；实现阶段需按本文件各验收边界测试。
