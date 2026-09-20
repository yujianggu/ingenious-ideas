# V1 编码验收记录

2026-09-20。范围：海外独立选题OS-B04的backend、frontend、app，以及它们之间的真实接口联调。未恢复旧海外10项。所有操作使用新建测试账号、合成可播放MP4和测试字幕，没有外部真实客户资料。

## 已执行结果

| 检查 | 结果 | 具体覆盖 |
| --- | --- | --- |
| 后端 `pytest -q` | 8通过 | 实际视频29/30/91秒边界、真实HTTP测试传输、隔离权限、会话/邀请过期、版本冲突、并发两写仅一成功、重开失效、实际MP4/SRT文件、质检交付签收、重启、完整备份恢复及权限 |
| 网页 `npm test` | 9通过 | API错误、401清理、失败保留表单、重复提交阻断、409重载、未保存状态、已有登录时邀请入口、预填字段可访问标签 |
| 网页 `npm run build` | 通过 | TypeScript检查、Vite生产构建 |
| 移动 `npm test` | 10通过 | 真实HTTP错误处理、版本绑定、审批条件、草稿保留/清理、账号数据重置 |
| 移动 `npm run typecheck` | 通过 | TypeScript检查 |
| 移动 `npm run build:all` | 通过 | Expo web、iOS、Android JavaScript/Hermes bundle导出 |
| 外部API集成 `api-e2e.mjs` | 55条HTTP断言通过 | 独立运行的真实API服务器、两工作室/两客户、修订、冲突、真实视频及字幕上传、下载、完整交付、签收、注销 |
| 网页浏览器 `web-e2e.cjs` | 通过 | 真正点击注册/邀请、客户建单、编辑开工、三段退回修订及批准、七份文件上传+QC、客户下载和签收；390px无横向溢出 |
| 移动网页 `app-e2e.cjs` | 通过 | Expo构建页面在390px运行真实API；保存兄弟片段保留草稿、未保存阻断送审、取消刷新、实际审稿、会话撤销后切换客户无前一客户缓存 |
| 独立复核 | 3项发现全部修正 | 移动草稿丢失、失效会话残留数据、备份/恢复文件权限；复核重新检查代码和实际0700/0600权限 |

人工查看桌面审稿页、网页手机交付页与Expo手机审稿页截图，布局与主要操作可读。浏览器场景无捕获到的页面JavaScript错误。后端测试有2条上游测试传输弃用警告，不影响本次通过结果。

## 依赖与环境

本机使用Python3.12、Node24.19.0、Chrome及Playwright1.62.1。网页和集成测试工具npm audit为0；Expo依赖已按SDK57兼容表锁定并通过依赖检查。移动依赖存在同一个上游uuid构建工具告警传播的11项moderate记录，无high/critical，详见app/README.md；未强制降级整个SDK来抹平告警。

## 复跑浏览器验收

先启动专用测试后端（独立EPISODE_DATA_DIR）和网页；不要对真实客户数据库运行这些建单测试。生成30秒9:16带音轨测试素材并安装集成工具：

```sh
# coding目录
backend/.venv/bin/python backend/tests/media_fixture.py /tmp/episode-desk-test.mp4
cd tests
npm ci
npm run test:api
npm run test:web
```

浏览器脚本使用已安装Google Chrome。可通过API_URL、WEB_URL、APP_URL及TEST_VIDEO覆盖默认地址与素材路径。网页默认5173、后端8000。移动浏览器测试需先在app执行 `npm run build:all`，然后从coding运行 `python3 -m http.server 8081 --bind 127.0.0.1 --directory app/dist`，后端CORS允许 `http://localhost:8081`，最后在tests执行 `npm run test:app`。

## 尚未执行的环境验收

- 本机没有Docker，未运行镜像构建/Compose启动；已提供配置，不能把应用本机通过写成Docker部署通过。
- 没有原生模拟器/真机、签名证书或商店账号；导出JavaScript bundle不等于签名安装包，SecureStore、原生文件选择/分享及平台权限需真机复验。
- 没有生产域名/TLS、云端运维、外部邮件、支付或真实商业订单；系统中的收款确认是操作人声明，人工内容质量仍由编辑验收。
- 本次是单实例业务验收，没有多实例高可用、媒体极限容量或规模性能承诺。
