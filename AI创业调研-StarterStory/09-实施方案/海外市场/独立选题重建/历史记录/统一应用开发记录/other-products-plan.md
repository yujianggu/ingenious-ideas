> 历史记录：统一应用目录已撤销。当前结构与验收见 ../../代码索引.md。此处测试数据仅描述拆分之前的版本。

# 其余海外项目开发计划与接口约定

2026-09-20：根据用户“继续，完成其他的”，将上一轮明确列出的 B06、B01、B09、C04、C14、C15、C17 纳入编码范围。B04 保持已有功能。旧海外十项已撤回，不恢复；本次软件开发不改写既有市场研究结论。

## 交付与架构

继续使用 coding/backend、coding/frontend、coding/app。共用账户、授权、存储、项目入口和基础表单，但七项各有独立数据、业务规则、状态转换和导出物。英语界面。所有新项目数据默认归当前用户私有，不能因同工作区而互读。B06 仅显式发布的站点开放公共页面和询盘入口。每次修改必须携带 revision；并发冲突保留输入并要求刷新。所有生成内容有清楚来源或模板说明，不能虚构 AI、发送邮件、收款、印刷或发货结果。

- B01：授权知识源、可追溯检索、带引用的待审回复、无依据时转人工、知识更新使旧审批失效、工单确认与导出。
- B06：本地清洁商网站资料、服务和区域、预览、发布快照、有效询盘与跟进、静态站点导出。发布指本服务的公共路径；域名上线仍由经营者配置。
- B09：公司级来源与事实、目标客户匹配、以核实事实生成草稿、审核和导出。无私人联系人采集和自动外联。
- C04：固定故事有限个性化、逐页编辑与成人校样、PDF、订单资料、经真实凭据记录印刷和配送。没有凭据不显示已履约。
- C14：行程资料、确定性的日历/结构化导入、时区、原始文件、离线设备缓存与导出。不能把文本摘要冒充原始票据。
- C15：可复用模块、人数/天数数量规则、模块去重合并、独立旅行清单、勾选、离线缓存及备份。
- C17：书/漫画/杂志统一目录、卷期数值排序、ISBN 校验、重复检测、CSV 导入导出、位置与缺期查询、离线缓存。

## 模块开发约定

后端每产品一个模块 coding/backend/app/products/<code_lower>.py，提供：

```
create(body: dict) -> dict  # 必须含 title；根层随后补 id/code/revision/timestamps
act(record: dict, action: str, body: dict) -> dict  # 在副本上变更并返回完整记录
export(record: dict, format: str) -> tuple[bytes, str, str] # 内容/MIME/文件名
```

校验失败使用 app.store.require/fail (422/404)。只写所属模块，不改 main.py/store.py/公共路由。日期/ID 可使用 app.store.now/ident。根层处理认证、隔离、事务、revision、元数据、JSON 备份。domain 不接受外部传入 owner/code/revision 覆盖。

接口 `/api/products/{code}/records` GET 返回 `{records:[...]}`，POST body 创建并返回记录；GET `/records/{id}` 返回记录；POST `/records/{id}/actions/{action}` body `{revision,...fields}` 返回记录；GET `/records/{id}/export/{format}` 下载。通用 JSON 导出格式 json。B06 另支持 `public_view(record)->str` 和 `public_action(record, body)->dict`；根层公开 GET `/api/public/sites/{id}` 和 POST `/api/public/sites/{id}/inquiries`，发布判定由模块执行，询盘提交响应只返回通用成功消息。

共享界面配置 `coding/shared/products/<code_lower>.json`：

```
{ "code":"B01", "name":"Answer Ledger", "summary":"…", "recordLabel":"Knowledge desk",
  "create":[Field],
  "actions":[{"id":"add-source","label":"Add source","help":"…","fields":[Field]}],
  "views":[{"path":"sources","title":"Sources","columns":[{"key":"id","label":"ID"},{"key":"title","label":"Title"}]}],
  "exports":[{"format":"txt","label":"Export answers"}]
}
```

Field: `{name,label,type,required?,default?,help?,options?:[{value,label}],optionsPath?,optionValue?,optionLabel?,min?,max?,accept?}`。type 为 text/textarea/number/checkbox/select/json/file/import。select 的 optionsPath 从 record 取数组，optionValue 默认 id，optionLabel 默认 title。json 输入 JSON 并解析；import 是可粘贴文本或读取 UTF-8 文件的 textarea；file 读取为 `{name,contentType,base64}`，单件限制 500 KiB，模块仍需验证类型和内容。所有表单都必须有明确业务字段和解释，避免把整份业务 JSON 交给普通用户填写。views.path 可为点分路径；列表显示 columns，标量/对象用可读键值显示；嵌套复杂值会可读展开。action 可用 `when:{path,equals}` 控制显示（可省略，服务端仍为最终校验）。输出结果不要只显示 ID，尽量提供可读名称/说明。

C14 附件统一数组 attachments，每项 `{id,name,contentType,base64,size}`。根界面支持其原始文件下载/离线打开。C15 离线勾选固定 action `toggle-item`，body `{itemId,checked}`，项目条目 `items` 含 id,label,quantity,checked；离线操作排队并带原 revision 回放，冲突不覆盖云端。其他项目离线只读缓存，编辑需联网。

## 执行顺序

1. 并行开发：B01/B09；B06/C04；C14/C15/C17。每组编写独立业务测试、配置、产品说明。
2. 根代理实现共用 API、Web 入口和业务表单、移动入口和离线缓存；身份和并发测试。
3. 各产品真实接口流程验证，Web/移动浏览器验收，B04 回归、构建导出。
4. 独立代码审查，修复明确缺陷，整理运行说明、功能覆盖和真实待接入事项。

不自动提交当前工作区其他线程修改；本轮没有新的 commit/push 指令。

## 实施时补充的界面约定

编辑字段可指定 `defaultPath` 从当前记录预填，或 `selectedFrom:{selector,path,value}` 在选择条目后预填对应字段；多行字符串数组转换为逐行文本。成功保存后用最新服务端数据重建表单，失败时保留输入。二进制附件仍限 500 KiB；文本文件选择器限 16 MiB，项目解析器各自执行更小格式限制；产品 JSON 请求限 32 MiB，最终记录限 12 MiB，备份使用紧凑 JSON。移动缓存按服务地址+账号隔离，退出与屏幕切换通过代次校验终止过期写入。

完成证据见 [验收记录](other-products-verification.md)。
