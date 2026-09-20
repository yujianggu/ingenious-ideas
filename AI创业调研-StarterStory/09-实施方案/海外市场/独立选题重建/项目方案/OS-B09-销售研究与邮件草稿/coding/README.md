# OS-B09 · 销售研究与邮件草稿

这是本 idea 独立的交付代码。后端、网页、移动端、配置、依赖和部署均在本目录中；不依赖仓库根目录或其他 idea 的源代码。

- `backend/`：FastAPI、独立 SQLite、账号和本项目业务 API。
- `frontend/`：React 网页，只包含本项目功能。
- `app/`：Expo / React Native，独立应用标识与本地会话。
- `deploy/`：独立容器与数据卷。
- `scripts/`：本项目启动与旧数据迁移。

## 本地启动

需要 Python 3.12、Node.js 24、npm。从本项目 `coding` 执行：

```sh
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
npm --prefix app ci
python3 scripts/dev.py
```

网页 http://127.0.0.1:5119，API http://127.0.0.1:8019/docs。首次注册本项目账号。其他 idea 的账号与会话不会自动共享。

移动端另开终端进入 `app`，执行 `npm start -- --port 8119`，使用开发客户端/Expo 支持的运行环境；浏览器预览 `npm run web -- --port 8119`。真机在 Connection 中填写局域网 API 地址，并用 `API_HOST=0.0.0.0 python3 scripts/dev.py` 启动后端。

## 检查

```sh
(cd backend && .venv/bin/python -m pytest -q)
(cd frontend && npm test && npm run build)
(cd app && npm test && npm run typecheck && npx expo export --platform all)
```

Expo 导出验证 JS/Hermes 产物；原生安装包、签名、商店审核仍需对应平台环境。部署见 [部署说明](deploy/README.md)。业务范围见 [交付边界](docs/scope.md)。

## 数据

默认保存在本项目 `backend/data`，可用 `B09_DATA_DIR` 指向独立目录。CORS 使用 `B09_CORS_ORIGINS`。不要将数据库、会话、密码或本地环境文件提交到 Git。

旧版统一应用的数据需显式迁移：`backend/.venv/bin/python scripts/migrate_legacy.py /绝对路径/episodes.sqlite3 /全新目标目录`。仅复制 B09 记录及其所属账号，不复制会话，不修改原库。随后设置 `B09_DATA_DIR` 为该目录并重新登录。
