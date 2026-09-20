# C04 个性化实体绘本 · Web

独立网页应用，仅包含本项目配置与私有账户入口。运行时不依赖其他 idea 或仓库根 coding。

要求 Node.js 20.19+，先启动同级 backend（端口 8024）。

```sh
cd frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:5124 。端口被占用时启动失败，不自动跳转。首次使用点击 Create an account，创建本项目独立账户。浏览器会话使用 `os-c04-token`。

```sh
npm test
npm run build
```

构建输出为 dist；部署时将 /api 代理到本项目 backend。不要直接公开开发服务器。
