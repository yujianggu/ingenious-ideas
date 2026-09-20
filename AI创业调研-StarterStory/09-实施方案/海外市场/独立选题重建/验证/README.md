# 七个独立项目的浏览器验证

`standalone-web-smoke.cjs` 读取上一级 `代码迁移清单.json`，支持相对于该目录的项目路径，也支持绝对路径。不会引用仓库根 coding 源码。

## 准备与运行

先分别为七个项目安装 backend 依赖，构建 frontend/dist 与 app/dist。各项目默认使用自己的 `backend/.venv/bin/python`。还需要 Node.js、Chrome，以及 B04 `coding/tests` 已安装的 Playwright。所有清单内 API、网页、App 端口必须空闲。

```sh
node standalone-web-smoke.cjs
```

可用 `PYTHON` 显式指定共享的验证解释器；它必须已安装七个后端的依赖并集，尤其包含 C04 所需的 ReportLab，不能只安装 B04 的 requirements。`PLAYWRIGHT_MODULE` 可指定其他已安装 Playwright 模块的绝对目录；默认是 B04 的 `coding/tests/node_modules/playwright`。`BROWSER_CHANNEL` 默认为 `chrome`。

## 检查范围

每个项目独立启动自己的后端、网页 dist 服务及 App dist 服务。通过网页 UI 注册、退出、登录、创建记录、执行业务流程，重新加载检查持久化，检查没有项目选择器或其他项目入口，并退出登录。还在 390px 宽度的 App 页面登录同一项目账户，读取网页创建的记录、检查无选择器和横向溢出，再退出。

业务覆盖：B01 知识来源与回复审核；B09 事实验证与邮件草稿审核；B06 发布快照、连续编辑及公开询价；C04 校样审核与 PDF；C14 ICS 导入与原始附件；C15 模块、数量规则；C17 目录缺期检测。

后端使用系统临时目录内的测试数据库。运行结束关闭浏览器、14 个静态服务、7 个后端，并移除测试数据库。截图、后端日志、PDF 与 results.json 保存在打印出的临时证据目录。脚本失败返回非零状态。不会写入项目正式数据目录。

## 已验证结果

2026-09-20：七个网页完整流程与七个 App 冒烟流程全部通过，无页面异常、无 390px 横向溢出；退出码 0。已确认全部测试进程退出、临时数据库清理。

本次证据目录：`/var/folders/0j/5x4kpg9x4_3gp48zsqtctjw40000gn/T/standalone-web-smoke-LV14T4`。该目录仅是本机运行证据，不是脚本运行依赖。
