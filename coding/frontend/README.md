# Episode Desk web

React + TypeScript client for the shared `/api` contract. Requires Node 22.12+ (verified with Node 24) and the backend on `127.0.0.1:8000`.

```sh
npm ci
npm run dev
npm test
npm run build
```

Vite serves the workspace at `http://127.0.0.1:5173` and proxies `/api` to the backend. Deploy `dist/` behind a same-origin reverse proxy that sends `/api` to the backend and falls back to `index.html` for application routes, including `/join`.

Register a studio, invite a client, and share the generated invitation URL. Client accounts are created by accepting those one-use invitations. No email is sent. Sessions use `sessionStorage` and authenticated file requests; there is no local role override or sample-data mode.

The web client covers brief authorization, source uploads, readiness, three-clip editing, versioned review, revisions, real final-file uploads and QC, delivery acceptance, copy/manifest downloads, source reopening, frozen snapshots and activity history. Server errors retain unsaved form fields. Concurrent edits show an explicit reload action; reloading warns before discarding unsaved changes. Sending a brief or review is disabled while changes remain unsaved.

Tests cover authentication expiry, validation-error normalization, revision-conflict recovery, retained drafts, duplicate submission prevention, dirty-form transition blocking, and invitation entry with an existing session. End-to-end service verification is documented in the parent project.

## 七个新增项目

登录后的项目导航提供 B01/B06/B09/C04/C14/C15/C17。业务表单和视图配置位于 `../shared/products`，共享界面位于 `src/products`。Vite/TypeScript 会直接打包共享配置；Docker 构建上下文必须为 coding（现有 compose 已配置）。运行边界见 [总说明](../README.md)，本轮结果见 [验收](../docs/other-products-verification.md)。
