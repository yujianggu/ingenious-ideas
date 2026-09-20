# Episode Desk mobile

Expo / React Native app for iOS and Android, with a web target for local review. It uses the real REST API in `../backend`; there is no demo database or offline mock mode.

## Run

Use Node 24 and npm (the verified runtime). From this directory:

```sh
npm ci
cp .env.example .env
npm start
```

Configure `EXPO_PUBLIC_API_URL` before starting Metro. The default is `http://localhost:8000/api`. For a physical phone, use a reachable LAN address in development or your HTTPS API in production. `localhost` on a phone refers to the phone itself. The sign-in screen also has a Connection setting; changing it clears the local session. Web previews require the backend CORS allowlist to include the preview origin.

```sh
npm run web
npm test
npm run typecheck
npm run build:all
```

`build:all` exports JavaScript bundles for all three targets; it does **not** produce signed iOS/Android binaries or prove device behavior. The web export is in `dist`. Serve it with a static HTTP server to inspect the mobile layout.

## Workflow

An editor creates a studio, generates a client invite and shares its one-use token manually. A client chooses **Join invite**, pastes the token and creates an account. Emails are not sent by this app. Clients can create episodes, complete and authorize the brief, upload a source, submit, review each version, request specific changes, download files and explicitly accept the final package.

Editors can assign episodes to clients, perform all four readiness attestations, edit the three clips and chapters, send reviews, start revisions, upload real files, confirm manual QC and deliver. Refresh the episode list after a new client joins to update the assignment list. Server validations remain authoritative; all updates carry the current revision, and review decisions also carry the version. A conflict offers an explicit reload. Failed mutations leave form values visible.

Reopening a brief resets source authorization, review approval and readiness. Package revisions clear approval and require fresh delivery files while preserving the due date. History and activity remain visible. Downloads include the review manifest and working copy/chapters; these exports do not substitute for real delivery files.

## Credentials and files

Native credentials are stored in Expo SecureStore; web credentials use sessionStorage. Downloads send Authorization headers, never tokens in URLs. Native downloads use a temporary private cache file and the system share/preview sheet, then remove the cache file after the sheet closes. Web downloads use an authenticated blob. Uploads use the system document picker and send a revision-aware multipart request. No fake upload success is displayed.

## Native build / release

Install Xcode with its iOS SDK for `npm run ios`, or Android Studio with its Android SDK for `npm run android`. Alternatively configure an Expo EAS project in your own account and build using EAS. Set your organization's bundle identifier/package name in `app.json`, production HTTPS API, signing credentials and store metadata before release. The included `com.episodedesk.app` identifiers are placeholders, not evidence of an owned store registration.

No signing credentials, store publication, paid accounts or push notification delivery are configured. Native simulator/device execution requires the matching SDK/runtime; web and JavaScript export checks alone cannot verify SecureStore, native file sharing or platform permissions on an actual device.

Dependencies are matched using the published Expo package's `bundledNativeModules.json`, not independent latest versions. See the lockfile for exact versions and the project verification report for actual check results.

## Verified dependency audit (2026-09-20)

The app is pinned to Expo 57.0.24 with React 19.2.3 and React Native 0.86.3 from Expo's published compatibility map. `npm audit` reports no high or critical findings. Eleven moderate entries are propagated from a single `uuid <11.1.1` advisory (`GHSA-w5hq-g745-h8pq`) through Expo's `xcode` configuration dependency. The suggested npm automatic fix is a downgrade to Expo 46; it was intentionally not applied. This is an outstanding build-tool dependency issue, not a claim of a clean audit. Recheck the upstream Expo dependency chain before store release.

Unsaved brief, clip, chapter and review-feedback edits are tracked independently. Saving a sibling or uploading a file preserves the other open drafts. Submit/send remain disabled until all changed forms are saved. Back, refresh, sign-out and connection changes ask before discarding drafts; a stale-version reload explicitly warns that it replaces them. Browser close/reload also warns where the browser supports `beforeunload`. Expired sessions and account replacement clear cached private episode/member/invite state before another account can view the app.

## 海外项目与离线功能

移动端现在包含 B04 及七个独立海外项目，显示名称为 Overseas Studio。共享配置由 `metro.config.cjs` 引入 `../shared`。C14/C15/C17 可主动保存本账号离线副本；C14 保留原始附件，C15 本地勾选支持版本检查后同步。退出账号和会话失效会清理该账号缓存；切换项目会取消旧屏幕后续存储写入。参见 [项目总览](../README.md) 和 [本轮验收](../docs/other-products-verification.md)。
