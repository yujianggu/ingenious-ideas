# B10 Task 7 独立交付报告

状态：实现、自审及最终回归完成，等待主线程独立审阅。无提交、无推送，不开展后续任务。

## 精确范围

本次仅在 B10 `coding/backend` 新增/修改：

- `src/service_b10/cli.py`：CLI 适配、输入/证据/候选与归档。
- `src/service_b10/model.py`：默认关闭的受控候选适配。
- `src/service_b10/exports.py`：确认后/历史版本导出的最小门禁扩展和可见状态。
- `tests/test_07.py`：29 项验收/防护用例（含参数化计数）。
- `README.md`：可运行中文命令、JSON、人工证据、模型边界与原型差异，保留 Task6 运维契约。
- 本报告。

未修改其他 idea、外层 coding、依赖锁或 Task1—4/6 的实现。以 task-6-fix3-snapshot 对比产品 src，仅新增 cli/model 和上述 exports 局部修改。

## 实现与契约

CLI 提供 init/new/submit/produce/review/deliver/return/confirm/amend/export/backup/restore；补齐 evidence、freeze-rules、resolve、receipt、status、resubmit，全部使用已有 Store/inputs/reporting/workflow/exports/backup API。

submit 一次接收一个明确 category/period 的文件，支持整份或单类 schema、原批次 ID 显式替代。produce 使用明确 period 读取当前批次，Task3 确定性复算，stage_revision 后记录 submit；所有状态事件要求同任务已登记的真实字节证据。提交后的输入不能直接覆盖，先 amend 再重新收件/生产。CLI 不把角色字符串或任意姓名当认证。

人工审阅后需 freeze-rules 和逐条 resolve，缺口径或未处理异常时无法 deliver。每次 deliver 返回当前 delivery_instance_id 与 snapshot_digest；receipt 命令只登记精确 structured receipt，保留 purpose、客户/任务/版本、交付实例、摘要、source_channel 与同任务 source_evidence。普通 blob、跨客户 blob、旧交付回执不能 confirm；同 revision 重新交付也必须新回执，旧 proof 历史保留。

主线程已同意的接口 ruling：Task5 原先仅允许 reviewed，无法诚实支持确认后归档。现保持 reviewed 原契约，扩展到含有效人工 review→deliver（及对应 confirm/return）事件的 delivered/accepted/returned；原状态和事件原样留存，拒绝 draft/submitted 及缺 review 的 accepted。工作簿 summary 显示状态，returned 明确“历史记录，不是有效终稿”。没有伪造 reviewed。

CLI export 先保存 `ROOT/client/task/exports/rREV-SNAPSHOT_HASH` 可备份原件，再原子无覆盖复制到外部 out。重试先验证已有相同快照归档；外部失败不删除归档。新状态对应新快照，旧归档不覆盖。底层 export_bundle 任意目录契约与 Task6 marker/相对路径登记保持不变。普通 CLI out 放工作区外，禁止写进其他客户目录。payload 读取在锁外，归档与副本发布边界只持一层 job_lock；不嵌套任何自带锁的 API。

输出为稳定 JSON：成功0，领域2，I/O1，意外内部失败1/INTERNAL_ERROR。stderr 仅稳定码及有效 task ID；argparse 不回显用户参数，异常消息和 traceback 不泄露原文/密钥/绝对路径。失败输入字节、最后有效批次、旧版本均不删除。

## 模型

`generate_candidate(payload, *, enabled=False, permitted=False, transport=None)` 关闭立即返回 None，不读配置/密钥、不建网络。CLI 默认流程完全不调用模型。启用必须有同任务授权原件与明确 CONFIG/AUTH；CONFIG、AUTH 与候选保留为 blobs，frozen input 引用其摘要。没有默认 model_id，必须显式给出评测记录编号和对应模型 ID，key 仅从 DASHSCOPE_API_KEY 读取；拒绝配置中夹带 api_key 等未声明字段。

只发送 metrics 与 source_ids，订单行/客户身份/文件名/原件不外发。金额从 Task3 calculate 复算；输出严格 JSON schema，来源存在、金额一致、usage 合法，额外 state=reviewed 被拒绝。人工/模型候选独立保存并固定 candidate；不直接写 reviewed 或篡改确定性报告。

标准库 HTTPS 固定精确 URL 白名单，所有 3xx 拒绝，无跨 host 跟随或国外回退。单次 timeout 0.1—30秒；仅 timeout 最多2重试；请求16KiB、响应64KiB，max_tokens≤2048，单命令 max_total_tokens≤16384；逐次请求保守预留输入字节+输出额度，超时也计入预留，防止重试无限花费。限制不等于账号/月度费用控制。

2026-09-20 通过官方文档核实北京 DashScope 兼容入口：

- https://help.aliyun.com/zh/model-studio/beijing-access-information
- https://help.aliyun.com/zh/model-studio/model-calling-in-sub-workspace

文档同时提示新业务空间专属域名；本次仅加入明确核实的 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，不通配允许新域名。真实 provider 账户连通、实际模型 ID 授权与质量评测均未执行，没有 API 付费调用。

## TDD 与验证证据

首次目标模块缺失：`15 failed, 1 passed in 0.94s`。随后用可导入、可运行但无业务实现的 CLI/model 骨架再次运行，得到 `15 failed, 1 passed in 0.89s`：CLI init 实际退出2而非0，授权/来源/金额/配置拒绝均未发生；不是缺依赖导致的红灯。

主体接入后：`1 failed, 15 passed in 29.65s`，唯一失败为完整 subprocess 流程走到 accepted 导出时原门禁返回 SNAPSHOT_NOT_REVIEWED。新增 returned 工作簿状态断言也先红，再最小扩展 exports。之后 `20 passed in 34.82s`。

新增配置证据持久化用例先因缺 model_config_evidence 红，修复后 `1 passed, 23 deselected in 3.72s`；禁止内联 key 用例先因没有拒绝红，修复后 `1 passed, 25 deselected in 0.51s`。

Task5+6+7 联合聚焦：

```text
.venv/bin/python -m pytest tests/test_07.py tests/test_05.py tests/test_06.py -q
87 passed in 65.49s (0:01:05)
```

首次完整回归（收尾自审修复前）：

```text
.venv/bin/python -m pytest tests -q
134 passed in 65.89s (0:01:05)
```

自审发现畸形 XLSX 的 BadZipFile/ParseError 会绕过错误映射并输出 traceback。新增真实坏 ZIP/坏 XML subprocess RED（`2 failed, 27 deselected in 2.00s`）及意外 RuntimeError 诊断 RED（`1 failed, 27 deselected in 0.68s`）。最小补齐格式领域错误和安全内部错误出口后：

```text
.venv/bin/python -m pytest tests/test_07.py -q -k 'corrupt_xlsx or unexpected_failure'
3 passed, 26 deselected in 1.83s
```

因为这是前次 full suite 后新发现的实际问题，修复后额外运行一次最终 full suite，不用先前通过冒充最终状态：

```text
.venv/bin/python -m pytest tests -q
137 passed in 66.94s (0:01:06)
```

最终29项Task7用例及所有前序Task1—6均包含在本次full suite，0失败，无skip。

其他验证：pip check 输出 `No broken requirements found.`；CLI --help 成功显示18个子命令；compileall 和精确5文件尾随空白检查 exit0。

README 的三个完整业务 bash 代码块实际提取为临时 zsh 脚本并以 `set -eu` 执行，exit0；包括第二客户建档、原版交付、退回、新修订与显式批次替代、新交付回执登记/确认、accepted/returned 双版本导出、backup/restore。没有仅靠文档字符串检查冒充运行。

## 关键验收覆盖

完整端到端测试用真实 subprocess 和临时工作区，注入 sitecustomize 禁止 socket 连接；默认关闭模型路径不可 skip。两客户分别收件生产，90000旧版退回、190000新版确认，两个状态/输入/事件互不串用；归档 manifest 由 Task5 API 验证，备份 bundles 相对路径全部恢复并重新验证；旧 proof 历史保留，旧/普通 proof 无法确认新实例。

覆盖单 revision return→resubmit→review→deliver 的新 delivery instance，再确认；人工候选不会直接 reviewed；无授权/无 key 时生产失败而源批次保留；非法金额原字节仍可从 Store 读取；跨客户 evidence 无法 review。模型注入 transport 覆盖 timeout三次上限、非JSON、未知source、金额篡改、非法state、usage与载荷上限、URL白名单；HTTP connection 注入覆盖实际 timeout 参数、3xx拒绝及非JSON provider envelope。未使用真实账户。

## 自审与未执行事项

- 已逐项核对非重入锁、同任务 evidence、修订/实例绑定、输出脱敏、确认后归档真实状态、相对bundle恢复。
- 工作区仍是内部单操作者工具；证据真实性、客户指定复核权、现实授权由人工采集/核实，不宣称本地参数实现登录或电子签名。
- CLI period沿用 Task2 YYYY-MM，周报用独立 task 并在口径记录实际区间；不是新增时间粒度协议。
- 模型文字本身的语义/业务解释仍需人工审核；结构和数字校验不能替代专家审阅。
- 真实客户文件、真实平台适配、业务专家/客户验收、Excel/WPS GUI 人工兼容、云端 provider 连接均未执行。Task5既有LibreOffice证据不扩称Excel/WPS。
- 备份仅完整性校验，不加密/签名，仍遵守 Task6 加密介质、保留期与删除授权要求。
- 完成本轮实现后停止，交主线程独立 review；不提交，不启动后续工作。

## Fix round 1：produce 快照与提交事件原子化

独立审阅唯一 Important/P2 已按最小范围修复，等待独立复审；本节为初稿137项全套验证之后的增量记录。只改 `workflow.py`、`cli.py`、`tests/test_07.py` 和本报告，Task4 修改已获主线程明确授权。

### 根因与修复

原 CLI 先调用 stage_revision 提交新草稿/替换草稿，再调用 record_event 单独提交 submit。后一事务失败时，前一事务已经持久化，留下半完成生产结果。

新增公开接口：

```python
stage_and_submit_revision(
    store, client, task, input_snapshot, report_snapshot, actor, reason,
    *, expected_revision=None,
) -> int
```

此操作在单层 Store.job_lock 内完成一次 BEGIN IMMEDIATE、保存/替换草稿、验证同任务 actor/evidence、写 submit 事件、更新 submitted 状态和最终 COMMIT。任一事件或 COMMIT 失败，SQLite 连接上下文回滚整个生产状态。首次生产不留下 revision；amend 后生产恢复原草稿快照，旧 revision/事件不变。

抽取 `_snapshot_json`、`_stage_in_transaction`、`_validate_event_reason` 和 `_event_in_transaction` 复用旧校验及 SQL，不复制领域逻辑。原 stage_revision/record_event 的签名、返回值、状态门禁与各自事务契约保持不变；事务内 helper 不加锁、不建表、不提交。`_schema` 的 executescript 只在 BEGIN 前运行，避免隐式提交切断原子边界。CLI produce 改为调用一次新接口；其他动作继续使用 record_event。

原件和先前已收件批次不会因失败删除。故障测试也验证 amend 前的90000报告、全部旧事件、amend复制出的草稿、新收件2000元订单及原1000元订单字节均仍在；撤销故障后重试只形成一个 submit 事件，并正确得到190000新结果。

### RED/GREEN

原独立审阅脚本原样执行，确认原问题：

```json
{"exit_code":1,"stderr":{"code":"IO_ERROR","task":"week1"},"persisted_revision":1,"persisted_state":"draft","persisted_amount":90000,"events":[]}
```

新增同一验收测试的四个参数化用例：首次生产/已有 amend 草稿 × submit事件失败/实际SQLite COMMIT失败。事件失败使用真实 SQLite trigger；COMMIT失败用 SQLite authorizer 在已插入新 submit 后拒绝实际 COMMIT，并确认此故障点确实触发。除该底层故障注入外，CLI/Store/输入/计算/workflow 均运行真实实现。

```text
.venv/bin/python -m pytest tests/test_07.py -q -k produce_atomic_snapshot
# 修复前：四个用例均在业务行未回滚的断言处失败
4 failed, 29 deselected in 0.95s
# 修复后：失败回滚、旧历史/输入保留及撤销故障重试均通过
4 passed, 29 deselected in 0.81s
```

原复现脚本 patch 的是旧 CLI 分开的 record_event 调用；修复后该调用点不再属于 produce，所以修复验证改用上述真实数据库事件/COMMIT故障点，不以绕过旧 monkeypatch 当作修复证据。

### 聚焦验证与边界

本轮只运行相关 CLI/workflow/backup 回归，覆盖原 API、端到端交付实例、历史导出与恢复；未重复无关 full suite：

```text
.venv/bin/python -m pytest tests/test_04.py tests/test_06.py tests/test_07.py -q
91 passed in 66.10s (0:01:06)
```

其中 Task7 当前33项（原29项加本轮4项），Task4 20项，Task6 38项；全部通过，无skip。

精确修改文件 compileall 和尾随空白检查均 exit0；已对 task-7-snapshot 比对，workflow 差异为上述公共原子接口与原事务逻辑抽取。未加新业务功能、未改依赖、未调用真实 provider、未提交/推送。交主线程复审后保持暂停。
