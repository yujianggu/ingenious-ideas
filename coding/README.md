# Overseas Studio — 海外独立项目

8 个海外项目共用登录和技术底座，各自保留独立业务模块与数据。代码由 `backend`、`frontend`、`app` 三端组成，新增项目入口位于登录后的项目导航。

| 项目 | 已实现的核心流程 | 使用说明 |
|---|---|---|
| B04 Episode Desk | 英语播客素材、三段审稿、真实文件质检、交付签收 | [原 B04 需求覆盖](docs/requirements-coverage.md) |
| B01 Answer Ledger | 授权知识源、带引用回复、人工审核、知识变更失效 | [B01](docs/products/B01.md) |
| B06 Local Clean Pages | 清洁商网站、发布快照、真实询盘与跟进 | [B06](docs/products/B06.md) |
| B09 Company Brief | 公司来源与事实、核实、研究卡和邮件草稿审核 | [B09](docs/products/B09.md) |
| C04 Story Keepsake | 个性化故事、8 页 PDF 校样、订单与实物履约记录 | [C04](docs/products/C04.md) |
| C14 Travel Dossier | ICS/CSV 预览导入、时区、原始文件、离线资料 | [C14](docs/products/C14.md) |
| C15 Pack Modules | 数量规则、模块合并、独立旅行清单、离线勾选同步 | [C15](docs/products/C15.md) |
| C17 Shelf Catalog | 混合书刊目录、ISBN 去重、卷期查缺、CSV 与备份 | [C17](docs/products/C17.md) |

来源是[海外独立选题重建](../AI创业调研-StarterStory/09-实施方案/海外市场/独立选题重建/README.md)。用户后来要求继续编码其余七项；软件完成不代表旧商业筛选已被市场验证，也没有恢复已撤回的海外十项。

```
coding/
  backend/     FastAPI + SQLite，账号、状态、私有文件与备份
  frontend/    React + TypeScript，编辑与客户网页
  app/         Expo + React Native，iOS / Android 移动端
  docs/        接口、实施与验证记录
  shared/      七个产品的业务表单与视图配置
  tests/       跨服务端到端验收
  scripts/     本地双服务启动
  deploy/      Docker + Nginx 单实例部署
```

## 本地启动

环境：Python 3.12、Node.js 24、npm，以及安装依赖所需网络。首次运行：

```sh
cd coding/backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd ../frontend
npm ci
cd ../app
npm ci
cd ..
python3 scripts/dev.py
```

网页：`http://127.0.0.1:5173`，后端交互文档：`http://127.0.0.1:8000/docs`。默认数据在 `coding/backend/data`，已忽略于Git。结束启动脚本会同时停止它启动的两个服务。

也可分别启动：

```sh
# 终端一，coding/backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 终端二，coding/frontend
npm run dev
# 终端三，coding/app
npm start
```

缺少系统npm时，启动脚本允许设置 `NPM_CLI=/绝对路径/npm-cli.js`，Node仍需在PATH。此项只适配开发工具环境，项目不依赖任何个人电脑的固定路径。

## 首次使用其他七个项目

注册并登录后，选择项目 → 新建业务记录 → 在“Next action”选择任务并保存。每个任务显示必要字段和前置条件，服务器会阻止跳过审核或使用旧版本。项目记录归当前账号私有；B04 的编辑/客户协作仍按原权限执行。B06 只有主动发布的内容会出现在公共路径，询盘提交不会泄露后台数据。

手机的 C14/C15/C17 点击“Save for offline use”后保存本账号资料，C14 同时保存附件原文。断网时可读，C15 可继续勾选；重连后主动同步，遇到云端修改会保留本地待同步项并提示冲突。可逐项查看云端勾选状态后决定是否丢弃本地项。退出账号删除本设备缓存；离线缓存位于应用私有存储（网页使用 IndexedDB），未额外实施文件级加密。网页预览的离线测试从已打开的应用开始，浏览器从零离线加载不在本版本范围内。

附件单件 500 KiB；文本导入文件最大 16 MiB，实际格式和项目限制见各项目说明；每条存储记录最大 12 MiB，每账号每项目最多 100 条。JSON 备份可通过 C15/C17 的恢复任务回导；其他项目导出用于审计/交付，整库恢复使用后端备份命令。

## 首次使用 B04 播客工作台

1. 网页注册工作室，成为编辑。没有内置演示密码或已登录角色。
2. 在成员区域创建客户邀请，手动将邀请链接交给客户。系统不会代发邮件。
3. 客户接受邀请设置密码，创建节目、填写品牌/源素材/时长/禁用声明，保存授权并提交。
4. 编辑确认素材可用、范围、交付能力与收款路径后开工，系统记录7个自然日的交期。
5. 编辑录入三个30–90秒片段的原话、上下文、标题和文案，逐项核对并提交审稿。
6. 客户逐段批准或退回；修订开启新整包版本并要求全部重新核对、批准。
7. 审稿全部通过后，编辑上传三个匹配时长的9:16 MP4、三个真实SRT与可编辑文字稿，完成质检后交付；客户下载并签收。

内容实际剪辑、转写和人工校核使用工作室现有工具。系统核验文件及流程条件；语义准确、字幕同步、构图和音质仍需编辑实际检查。开工勾选记录操作者声明，不代表已自动收款。

## 手机连接

`app/.env.example`说明API地址；真机不能使用开发电脑的localhost。开发时在可信局域网启动后端 `--host 0.0.0.0`，将 `EXPO_PUBLIC_API_URL` 设为电脑局域网地址并重新启动Expo。正式环境使用HTTPS。网页和移动端读取同一服务端数据。

移动端源码、类型检查和JavaScript打包与已签名安装包是不同交付环节；iOS/Android商店发布需要相应开发者账号、签名及设备验收，详见[移动端说明](app/README.md)。

## 验证与部署

[B04 接口协议](docs/api-contract.md) · [其他项目架构与接口](docs/other-products-plan.md) · [其他项目验收](docs/other-products-verification.md) · [验证记录](docs/verification.md) · [部署说明](deploy/README.md)

后端测试在 `backend` 下执行 `.venv/bin/python -m pytest -q`；网页在 `frontend` 下执行 `npm test && npm run build`；移动端在 `app` 下执行 `npm test && npm run typecheck && npm run build:all`。外部HTTP集成脚本为 `tests/api-e2e.mjs`，只能对专用测试数据库运行，它会创建工作室、账号、订单和上传素材。

生产数据库保持私有；服务器数据目录和备份都含客户资料，不纳入源码。备份及恢复命令见[后端说明](backend/README.md)。Docker设计为单后端进程、本地持久卷；多副本部署前需迁移数据库、会话与文件存储。

## 外部服务边界

B01/B09 使用确定性文本检索和模板，保留来源与人工审核，不调用未配置的模型、不抓取网页或发送邮件。C04 PDF 是固定故事校样；付款、印刷和物流必须实际发生后录入凭据，系统没有代收款或代下印刷单。B06 公共页面运行于当前服务，域名、HTTPS、主机和获客由部署经营者配置。原生应用签名、真机验收与商店发布需要实际开发者账户和设备。
