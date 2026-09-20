# B09 移动端

独立 Expo 应用，只使用本 idea 的 API 和 shared 配置。

```sh
npm ci
npm start
npm run typecheck
npm test
npm run build:all
```

默认 API `http://localhost:8019/api`，Expo 端口 8119。真机需在 Connection 中设置电脑的局域网 API 地址，或设置 `EXPO_PUBLIC_API_URL`。

登录凭据和离线缓存按项目、服务器和账户隔离；退出、切换服务器或 401 失效会删除当前账户缓存并取消迟到写入。离线启动可恢复已记住的会话，恢复网络时服务器仍会校验凭据。
