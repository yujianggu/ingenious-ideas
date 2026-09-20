> 以下保留原 B04 开发记录；目录迁移后的验证以独立选题重建目录的 idea维度迁移验收.md 为准。命令均从本项目 coding 执行。

# OS-B04 coding execution ledger

Plan: implementation.md. Branch: codex/overseas-b04-implementation.

- Scope: existing approved OS-B04 workflow; user requests all three coding directories. Old mirrored Top10 remains withdrawn.
- Historical decision: the original repository-root coding location was later corrected. The current code lives inside this B04 idea; see [current code index](../../../../代码索引.md). Domestic project changes remain out of scope.
- Decision: native mobile uses Expo while optional platform clarification is pending; no external service purchase, messages, or publishing.
- Task backend: build_backend, complete; owns backend.
- Task web: build_frontend, complete; owns frontend.
- Task mobile: build_mobile, complete; owns app.
- Task integration: root, owns docs/scripts/tests/deploy. All share fixed REST contract.
- Interface review: backend produces revision/version state and authorization; web/app consume identical camelCase contract. File upload mutates revision. Both clients must use returned aggregate, not cached approval.
- Persistence decision: single-instance SQLite with transactional optimistic writes; PostgreSQL migration and multi-node deployment outside this initial service product. Docker restricted to one backend worker.
- Git: no commit or push requested for this development turn.

- Integration complete: 8 backend +9web +10mobile tests pass; 55 realHTTP assertions; fullbrowserdelivery +Expo mobile UI pass.
- Review: 3mediumissues fixed and independently rechecked (draftretention, accountcacheclear, privatebackupmodes).
- Native JS exports pass; Docker/nativebinary/device/TLS/cloud deployment not executed. Full evidence verification.md.

- Final media boundary regression: actualMP4duration30–90 seconds required, in addition to approvedclipduration matching;29/91 rejected,30 accepted.
