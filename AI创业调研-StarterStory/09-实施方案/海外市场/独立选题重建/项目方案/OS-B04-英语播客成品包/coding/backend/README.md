# Episode Desk backend

Requires Python 3.12. From `coding/backend`:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
EPISODE_DATA_DIR=./data .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest -q
```

`GET /api/health` reports process health; `/docs` documents the routes. See `../docs/api-contract.md` for workflow, request fields and errors. `create_app(data_dir=...)` is the isolated application factory. Environment variables:

- `EPISODE_DATA_DIR`: private writable SQLite and files directory (default `data` relative to working directory).
- `EPISODE_CORS_ORIGINS`: comma-separated explicit browser origins (default `http://localhost:5173,http://localhost:8081`).
- `EPISODE_MAX_UPLOAD_BYTES`: per-file streaming limit (default 104857600).

Run one application instance with persistent local disk; use TLS at a reverse proxy and also enforce request-body/time limits there. Do not expose the data directory as static files. Passwords use PBKDF2-SHA256 with random salts and 600,000 rounds. Random bearer sessions expire after seven days, are stored as hashes, and revoke on logout. Authentication endpoints permit 30 requests per source IP per minute (in-process limiter). Configure proxy trust to prevent forged client IPs. Invites expire in three days and are single use. No email delivery or charging is simulated.

SQLite `BEGIN IMMEDIATE` serializes revisions and file-reference updates. Upload bytes and media checks occur outside the transaction; revision and session are rechecked before commit, and failed uploads are deleted. Successful replacements remove previous bytes after commit. Downloads open a private file handle before releasing the database transaction. A process crash can leave an unreferenced private file; backups only copy database-referenced files. MP4 decoding verifies video and audio tracks, portrait aspect and duration; this is technical validation, not an automated judgment of content, copyright or editorial quality. Human QC remains explicit. Delivery revalidates every current final file.


本目录仅包含 B04。统一运行入口见 [项目说明](../README.md)。

本地默认 CORS 同时允许本项目网页和移动网页端口的 `localhost` 与 `127.0.0.1`；不允许其他 idea 的端口。自定义来源用对应 CORS 环境变量完整覆盖。
