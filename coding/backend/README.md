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

## Backup and restore

```sh
.venv/bin/python -m app.backup backup ./data /safe/location/backup-20260920
.venv/bin/python -m app.backup restore /safe/location/backup-20260920 ./restored-data
```

Backup holds the SQLite writer lock while taking a SQLite snapshot and copying only referenced private files, then writes SHA-256 manifest checksums. Destination must be absent or empty and outside the data directory. Restore verifies checksums before copying, refuses nonempty destinations, and preserves accounts, session hashes, episode state and media. Backup and restore directories are created with mode 0700; databases, manifests and copied files have mode 0600 independently of umask or source-file permissions, including before a restored server starts. Stop the destination server before restoration; point `EPISODE_DATA_DIR` to the restored directory afterwards. Backup directories contain sensitive data and require restricted permissions and appropriate encrypted storage. Test restores regularly. Never merge a restore into a live directory.

Generate a real test video with `.venv/bin/python tests/media_fixture.py /tmp/episode-desk-test.mp4`. Tests exercise the API through Starlette's HTTP test transport; the root integration suite uses a real network server. No cloud deployment or app-store submission is performed by this backend.

## 七个海外产品模块

`app/products` 中 B01/B06/B09/C04/C14/C15/C17 各有独立规则，`gateway.py` 处理用户隔离、事务与版本。首次启动自动创建 product_records 表及索引，原 B04 数据保留。完整 SQLite 备份同时包含新项目数据及内嵌附件。服务端产品请求上限 32 MiB，单条最终记录 12 MiB；二进制附件仍由 C14 单独限制 500 KiB。C04 PDF 使用锁定版本 ReportLab/Pillow。接口与字段见 [总计划](../docs/other-products-plan.md)，结果见 [验收](../docs/other-products-verification.md)。
