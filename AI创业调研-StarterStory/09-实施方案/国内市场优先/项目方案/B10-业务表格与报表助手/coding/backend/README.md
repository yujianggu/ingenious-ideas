# B10 本地业务表格与报表助手

这是供交付人员运行的真实本地 CLI：CSV/XLSX 收件、确定性金额计算、来源追溯、人工审阅、口径与异常处理、交付及客户确认、版本化 XLSX、备份恢复。模型默认关闭。它没有客户在线门户、云账号或租户登录；client/task 是内部隔离标识，不是身份认证。

07 原型用浏览器手填汇总和角色切换演示流程；这里读取真实文件、保存 SQLite 与不可变原件、校验状态/证据及摘要。配置化样例不等于已适配真实平台字段，自动测试不等于真实客户验收或 Excel/WPS 人工兼容验收。

## 环境与退出码

在本目录运行。现有 `.venv` 使用 Python 3.12.14，依赖见 `requirements.lock`（含精确版本与哈希）；不使用系统 Python 3.9。已有环境直接运行：

```bash
source /Users/yuwujian/.local/share/ingenious-ideas/toolchains/env.sh
.venv/bin/python -m service_b10.cli --help
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pip check
```

成功退出 0，参数/领域失败 2，文件或 SQLite I/O 失败 1；未预期的内部失败同样返回 1 和 `INTERNAL_ERROR`，不泄露异常原文。stdout 为机器可读 JSON 结果，只含稳定码、task、版本、状态或摘要/批次 ID；stderr 仅稳定错误码及有效 task ID，不打印原始资料、密钥或绝对路径。运行产物、真实资料和备份应放仓库之外。

常见错误：`SNAPSHOT_FROZEN` 先 amend；`REPLACEMENT_REQUIRED` 传前次 batch_id；`METRIC_RULES_NOT_FROZEN` 先 freeze-rules；未处理差异须 resolve；`CUSTOMER_CONFIRMATION_PROOF_REQUIRED` 重新取得当前交付的客户确认；`VERSION_EXISTS` 换新的导出/备份路径。失败不会删除用户输入或旧版本；已收下但解析失败的源字节保留为 task blob。每次 submit 只登记一个类别，不会在一个多表命令里掩盖部分成功。

## 可直接运行的虚构人工流程

以下例子使用临时目录，不读取任何真实客户资料。实际运行把临时目录换成受保护、仓库外的固定工作区。所有样例签认均为虚构演示；正式操作须先取得真实邮件/签字文件并由人工核验身份和授权。

```bash
export B10_DEMO="$(mktemp -d)"
export B10_ROOT="$B10_DEMO/live"
b10() { .venv/bin/python -m service_b10.cli "$@"; }
job() { b10 "$1" --root "$B10_ROOT" --client alice --task week1 "${@:2}"; }
b10 init --root "$B10_ROOT"
job new
b10 new --root "$B10_ROOT" --client bob --task week1
cat > "$B10_DEMO/orders.csv" <<'EOF'
店铺,订单号,实收
a,1,1000.00
EOF
cat > "$B10_DEMO/refunds.csv" <<'EOF'
退款号,店铺,订单号,退款金额
r1,a,1,100.00
r2,a,9,2.00
EOF
cat > "$B10_DEMO/ads.csv" <<'EOF'
广告费
50.00
EOF
cat > "$B10_DEMO/review-evidence.txt" <<'EOF'
虚构演示：交付人员核对来源与金额，客户指定复核人确认本期口径和跨期退款排除说明。
正式证据须标实际人员、采集时间、邮件/签字来源及确认范围，不能只写姓名。
EOF
job evidence --input "$B10_DEMO/review-evidence.txt" > "$B10_DEMO/evidence-result.json"
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['B10_DEMO'])
digest=json.loads((p/'evidence-result.json').read_text())['evidence']
(p/'actor.json').write_text(json.dumps({'name':'虚构交付员','channel':'operator','evidence':digest},ensure_ascii=False))
(p/'rules.json').write_text(json.dumps({'version':'人工口径v1','paid_less_refund':'本期实收减匹配退款，不称净利润','ads':'缺广告为不可计算；本例50元'},ensure_ascii=False))
PY
job submit --category orders --period 2026-09 --input "$B10_DEMO/orders.csv" --schema config/schema.example.json > "$B10_DEMO/orders-result.json"
job submit --category refunds --period 2026-09 --input "$B10_DEMO/refunds.csv" --schema config/schema.example.json
job submit --category ads --period 2026-09 --input "$B10_DEMO/ads.csv" --schema config/schema.example.json
job produce --period 2026-09 --actor-record "$B10_DEMO/actor.json"
job review --revision 1 --actor-record "$B10_DEMO/actor.json"
job freeze-rules --revision 1 --actor-record "$B10_DEMO/actor.json" --rules "$B10_DEMO/rules.json"
job resolve --revision 1 --actor-record "$B10_DEMO/actor.json" --refund-id r2 --resolution exclude --reason '跨期退款，保留异常，不计入本期'
job deliver --revision 1 --actor-record "$B10_DEMO/actor.json" > "$B10_DEMO/delivery.json"
job status --revision 1
```

schema 的实际列映射见 `config/schema.example.json`；可传完整三类 schema 或单类对象。CSV 必须约定编码、千分位、精度与币种；XLSX 可在对应 schema 增加 `"sheet":"Sheet1"`。同期间相同字节重传返回原批次，新字节需要 `--supersedes OLD_BATCH_ID`。不收 ads 时广告为不可计算；广告确实为 0 时应提交金额 0 的文件。输入期间目前使用 `YYYY-MM` 协议，每份周报用独立 task（例如 week1），周期更细的业务语义记入口径。

`produce` 确定性计算并把草稿提交为 submitted，`review` 才由人工推进 reviewed；freeze-rules 与 resolve 必须发生在 reviewed，且一次冻结后不可改写。口径/差异变更需新修订。人工角色 JSON 必须引用同 client/task 内 evidence 命令返回的真实 blob 摘要；跨客户摘要不能复用。

## 退回、修订与重新交付

```bash
job return --revision 1 --actor-record "$B10_DEMO/actor.json" --reason '客户邮件反馈实收漏单；原邮件已入库'
job amend --revision 1 --actor-record "$B10_DEMO/actor.json" --reason '补全漏单，新版重新审核'
cat > "$B10_DEMO/orders-v2.csv" <<'EOF'
店铺,订单号,实收
a,1,2000.00
EOF
OLD_BATCH=$(.venv/bin/python -c 'import json,os; from pathlib import Path; print(json.loads((Path(os.environ["B10_DEMO"])/"orders-result.json").read_text())["batch_id"])')
job submit --category orders --period 2026-09 --input "$B10_DEMO/orders-v2.csv" --schema config/schema.example.json --supersedes "$OLD_BATCH"
job produce --period 2026-09 --actor-record "$B10_DEMO/actor.json"
job review --revision 2 --actor-record "$B10_DEMO/actor.json"
job freeze-rules --revision 2 --actor-record "$B10_DEMO/actor.json" --rules "$B10_DEMO/rules.json"
job resolve --revision 2 --actor-record "$B10_DEMO/actor.json" --refund-id r2 --resolution exclude --reason '复核跨期退款，保留异常'
job deliver --revision 2 --actor-record "$B10_DEMO/actor.json" > "$B10_DEMO/delivery.json"
```

无内容修改、只重新审核可用 `job resubmit --revision 1 --actor-record ...`（仅 returned 可用），再 review、deliver。即使 revision 不变，再次交付也产生新的 delivery_instance_id 与 snapshot_digest，旧回执不能确认新交付。

## 客户确认凭证：原始证据与结构化绑定分开保存

先采集客户实际邮件/签字文件，核验其针对的报告版本、交付实例和摘要；操作者不能凭任意姓名或普通附件直接 confirm。下面演示生成结构化回执，仅展示文件格式，不代替真人签认。

```bash
cat > "$B10_DEMO/customer-email.txt" <<'EOF'
虚构演示邮件：客户指定复核人已核对第二版1900元实收减退款和50元广告，同意本次交付。
实际邮件必须确实针对 delivery.json 所示交付；记录发件来源、时间、版本和确认范围。
EOF
job evidence --input "$B10_DEMO/customer-email.txt" > "$B10_DEMO/customer-source.json"
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['B10_DEMO'])
d=json.loads((p/'delivery.json').read_text())
s=json.loads((p/'customer-source.json').read_text())
r={'evidence_kind':'customer_confirmation_receipt','purpose':'confirm_delivery',
   'client':'alice','task':'week1','revision':2,
   'delivery_instance_id':d['delivery_instance_id'],'snapshot_digest':d['snapshot_digest'],
   'source_channel':'email','source_evidence':s['evidence']}
(p/'receipt.json').write_text(json.dumps(r,ensure_ascii=False))
PY
job receipt --revision 2 --input "$B10_DEMO/receipt.json" > "$B10_DEMO/receipt-result.json"
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['B10_DEMO'])
d=json.loads((p/'receipt-result.json').read_text())['evidence']
(p/'customer.json').write_text(json.dumps({'name':'虚构客户复核人','channel':'customer','evidence':d},ensure_ascii=False))
PY
job confirm --revision 2 --actor-record "$B10_DEMO/customer.json" --reason '客户明确确认'
job export --revision 2 --out "$B10_DEMO/delivery-v2"
job export --revision 1 --out "$B10_DEMO/history-v1"
b10 backup --root "$B10_ROOT" --out "$B10_DEMO/backup-1"
b10 restore --manifest "$B10_DEMO/backup-1/manifest.json" --out "$B10_DEMO/restored"
```

receipt 必须精确包含上述 9 项字段；`source_channel` 只允许 email、signed_document、portal、in_person。source_evidence 与 receipt 必须是两份不同的同任务原件。CLI 验证结构、来源归属、摘要与当前 delivery instance，不鉴定邮件真伪或提供电子签名。实际身份、采集方式及客户指定复核权由交付流程人工核实。

导出目录包含 `report.xlsx`、`snapshot.json`、`manifest.json` 与隐藏身份标记。四张工作表是 summary、sources、exceptions、rules；summary 标示真实版本状态。returned 可用于历史留存但明确不是有效终稿，draft/submitted 不能导出。客户确认后的 accepted 状态和所有事件原样保留，不改写成 reviewed。

CLI 始终先在 `ROOT/client/task/exports/rREV-SNAPSHOT_HASH` 保存可备份原件，再原子复制到 `--out`。外部 out 不覆盖；失败时工作区原件可用于重试。普通 out 必须在 Store 之外，以免跨客户放置。底层 `export_bundle` 的任意目标目录 API 保持不变。重试可复用经验证的相同归档；新状态快照生成新归档。备份列出所有归档的相对路径，恢复时逐个验证。

## 可选人工候选与模型候选

人工文字候选可通过 `produce --candidate candidate.json` 传入：

```json
{"text":"实收减退款需与跨期差异一并阅读","source_ids":["submit返回的source_id"],"metrics":{"paid_less_refund":90000,"ads":5000},"usage":{"total_tokens":0}}
```

金额使用整数分；source_ids 必须来自当前批次；数字必须与确定性计算一致。候选独立保存为不可变 blob，状态为 candidate，不修改金额或直接推进 reviewed；仍须 review。任意文字解释的业务正确性还需人工核验。

模型关闭路径不读取密钥、不建网络连接；完整 subprocess 验收使用 socket 禁用护栏证明这一点。启用需要同时传 `--enable-model --model-config CONFIG --model-authorization AUTH`。不传授权拒绝，传 config 但不启用仍不会联网。密钥只读环境变量 `DASHSCOPE_API_KEY`，禁止写入 JSON、仓库或日志。本次没有真实账户调用。

CONFIG 的全部字段如下（占位模型 ID 必须由本地评测/授权后的明确实际 ID 替换，不提供浮动默认模型）：

```json
{"endpoint":"https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions","model_id":"替换为已评测且已获授权的模型ID","evaluation_record":"替换为评测记录编号","timeout_seconds":10,"max_tokens":512,"max_total_tokens":12000,"retries":2}
```

AUTH 格式如下，evidence 先用 evidence 命令存入本任务，原件应说明允许外发的范围、接收端和实际授权来源：

```json
{"purpose":"authorize_model","model_id":"与CONFIG完全一致","endpoint":"https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions","allowed_fields":["metrics","source_ids"],"evidence":"本任务授权原件摘要"}
```

只发送脱敏指标和来源 ID，不发送订单行、姓名、原文件名、原件或授权文本。结果必须为 JSON，来源必须存在，金额字段必须通过 Task3 复算，候选仍待人工审核。每次请求 socket timeout 限 0.1–30 秒，仅 timeout 最多重试 2 次（总计最多 3 次）；超时也预留用量，不能认为未计费。请求上限 16 KiB、响应上限 64 KiB、输出上限 2048 tokens，总预留上限 16384 tokens/调用；总额度不足则提前拒绝。限制是单次命令预算，不是账号/月度额度或价格保证。

标准库 HTTPS 固定精确白名单 URL，所有 3xx 拒绝且不跟随重定向；没有国外回退。当前仅支持已核实的 DashScope 北京兼容域名（官方将其定位为存量兼容入口）；不接受任意 host 或自动发现新端点。2026-09-20 核实依据：[官方地域及接入域名](https://help.aliyun.com/zh/model-studio/beijing-access-information)、[官方子业务空间模型调用](https://help.aliyun.com/zh/model-studio/model-calling-in-sub-workspace)。业务空间专属新域名需要单独评审后增加精确白名单，不在本次实现中。端点文档核实不等于真实云端连通、模型授权、效果评测或地域合规验收。

## 备份与保留要求

备份输出必须在 Store root 与仓库外；ZIP 有完整性校验但**没有加密**，必须放在已验证的 OS 加密介质，恢复密钥与工作区/备份分开保存。restore 目标必须不存在，验证全部摘要、SQLite、附件引用与导出归档后才原子发布；不替换运行中的 live Store。核验新目录后再由操作者决定是否切换。

所有已有 Store/import/workflow API 自带非重入锁，不得再次嵌套 job_lock；导出到 Store 内只在一次发布边界加一层锁。当前 CLI 对归档及副本发布持有同一层锁。

不自动删除未验收资料。保留期、验收、法律保留与删除授权按收件约定人工确认；删除清单需覆盖所有原件、候选、工作簿、快照及每一份轮换备份，记录批准人/操作者/时间/验证结果。先恢复验证保留的替代备份，再轮换旧副本。详细既有备份运维契约如下。

## 既有备份 API 运维契约

The dependency lock is generated with `pip-tools 7.6.1` under Python 3.12.

## Backup and restore operations

`backup(Store(root), output_directory)` creates `manifest.json` and
`payload.zip` in a new output directory. The database is captured with the
SQLite Backup API while the shared job lock is held; the live database file is
never copied directly. The payload also contains every registered immutable
source/evidence blob and every `export_bundle` directory stored anywhere below
the Store root. New bundles carry `.service-b10-export.json`; backup records
their relative directories independently of the bundle manifest, so a missing
or malformed manifest/member fails restore. Intact pre-marker Task 5 bundles
are discovered from their valid manifests for backward compatibility. Ordinary
customer directories and unrelated JSON files are not treated as bundles.
`restore(manifest, new_root)` accepts only a path that does not exist, verifies
the complete manifest, archive limits, SQLite integrity/schema/foreign keys,
all registered blobs, and included export bundles, then publishes `new_root`
atomically. Task 7 can pass the manifest returned by `backup` directly to
`restore`.

All mutations below a Store root must participate in `Store.job_lock`.
`Store.create`/`put`, imports, and workflow writes already do so; schema
initialization on read-facing import/workflow APIs also holds the lock. Do not
wrap those APIs in another `job_lock` because the file lock is intentionally
non-reentrant. If Task 7 publishes an export bundle below the Store root, it
must hold one outer `job_lock` around `export_bundle` only. Restore never
replaces a live root: its target must not exist, and an atomic no-replace
publish rejects a process that creates the target during validation.

The generated ZIP is an integrity-checked backup, **not an encrypted backup**.
Keep backup output outside the repository and on OS-encrypted storage (for
example, FileVault/APFS encrypted media or an encrypted enterprise backup
volume). Before operation, record that encryption is enabled, recovery was
tested, and the recovery key is held separately from both the live workspace
and backup media. Do not put recovery keys, API keys, or passwords in the
manifest, ZIP, repository, or task logs.

Retention is an operator-approved setting, recorded per deployment as
`retention_days` together with data owner, legal/contract basis, approval
identity, approval evidence, approval date, and next review date. Version 1
does not automatically delete any unaccepted or unverified material. A manual
deletion record must name the client/task scope, accepted revision, raw
imports/evidence, derived workbooks/snapshots, live workspace, and every backup
rotation copy; it must record the exact targets, approver, operator, timestamp,
and verification result. Restore-test the retained replacement backup before
rotating an older copy. If any approval, acceptance, legal hold, target list,
or replacement verification is missing, defer deletion.
