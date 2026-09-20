# C17 部署

在本项目 `coding` 内执行 `docker compose -f deploy/compose.yml up --build -d`。访问 http://localhost:6137。独立 Compose 项目与数据卷，不共享其他 idea 数据。后端仅容器网络可见，前端代理 `/api`。移动端连接外部 HTTPS 入口。正式服务需配置域名、TLS、备份与恢复演练；当前环境未运行 Docker 验证。
