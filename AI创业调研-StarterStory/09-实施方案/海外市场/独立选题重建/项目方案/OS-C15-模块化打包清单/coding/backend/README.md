# C15 模块化打包清单后端

此目录是独立 FastAPI 服务，只包含 C15 领域。Python 3.11+。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8035
```

生产安装使用 `requirements.txt`。开发与测试依赖在 `requirements-dev.txt`，均固定版本。
`C15_DATA_DIR` 默认本 backend 目录的 `data/`，私有数据库 `project.sqlite3`（目录 0700，数据库 0600）；`C15_CORS_ORIGINS` 为逗号分隔的允许来源，默认 `http://localhost:5135,http://localhost:8135`。

账户 API 为 `/api/auth/register`、`login`、`me`、`logout`。注册 JSON 需 `name,email,password,workspaceName`；会话为七天 Bearer token。保留客户端兼容的用户 `role,workspaceId` 字段，业务记录只按用户 ID 私有隔离。数据库仅有 users、sessions、product_records 三表，不含邀请、播客或其他领域表。

领域 API 根路径 `/api/products/C15/records`；支持列表、创建、详情、`actions/{action}`、`delete`、`export/{format}`。更改和删除必须提交当前整数 `revision`；删除还需 `confirm:true`。其他 code 与播客/邀请 API 返回 404。私有导出禁止缓存。每账户最多 100 条记录，单条记录最多 12 MiB，领域请求体上限 32 MiB，其余请求 1 MiB，认证每 IP 每分钟最多 30 次。领域模块保留各自额外业务约束。

不安装任何公开站点路由。

本地默认 CORS 同时允许本项目网页和移动网页端口的 `localhost` 与 `127.0.0.1`；不允许其他 idea 的端口。自定义来源用对应 CORS 环境变量完整覆盖。
