# REST API contract v1

Base /api. JSON camelCase. Bearer opaque token. Error `{detail: string}`; validation errors may be FastAPI array and clients normalize. Invalid 422, unauthenticated401, forbidden403/notfound404, stale revision409. All successes return JSON unless file or204. Authentication responses `{token,expiresAt,user:{id,name,email,role,workspaceId}}`; roles editor/client. Password >=10 characters. Public register makes a new studio/editor, no role selector.

## Auth/workspace

POST /auth/register `{name,email,password,workspaceName}` -> session
POST /auth/login `{email,password}` -> session
GET /auth/me -> user
POST /auth/logout -> `{ok:true}` (revokes current token)
POST /invites `{email}` editor -> `{token,email,expiresAt}`; frontend invite URL `/join?token=...`; no actual email sending.
POST /auth/accept-invite `{token,name,password}` -> session, one-use invite, new client in inviting workspace.
GET /members -> `{members: User[]}` (editor; all workspace members)

## Episode shape

`{id,title,brand,source,duration,notes,glossary,prohibitedClaims,rights,clientId,phase,version,revision,createdAt,updatedAt,readyAt,dueAt,clips,chapters,history,events,assets}`
phase: draft/submitted/editing/review/approved/delivered/accepted.
version starts1; revision starts1 and increases on EACH successful mutation (including file/QC). All mutations except creation carry expected `revision`. Returned full Episode is authoritative. history contains `{version,sentAt,title,source,duration,chapters,clips,decisions}`; decisions `{clipId,status,feedback,actorId,time}`. Events `{id,action,actorId,actorName,time,version}`. Dates ISO UTC.
clips exactly3: `{id:'clip-1'|'clip-2'|'clip-3',title,start,end,quote,context,post,checked,status,feedback}`; statuses draft/pending/approved/changes. All text required before send. start/end integerseconds,30–90 duration, within90–3600source. Chapters plain text required.
assets: `{id,kind,clipId,version,filename,size,contentType,checked,createdAt}`; source/project clipId null; video/subtitle clipId required. Server-generated IDs. Never expose filesystem paths. current assets may include old versions; UI clearly labels/filter current version.

GET /episodes -> `{episodes: Episode[]}` only permitted episodes.
GET /episodes/{id} -> Episode
POST /episodes `{title,brand,source,duration,notes?,glossary?,prohibitedClaims?,clientId?}` -> Episode; clientId=self forclient, editor chooses workspaceclient.
PUT /episodes/{id}/brief `{revision,title,brand,source,duration,notes,glossary,prohibitedClaims,rights}` client only draft.
PUT /episodes/{id}/clips/{clipId} `{revision,title,start,end,quote,context,post,checked}` editor onlyediting.
PUT /episodes/{id}/chapters `{revision,chapters}` editor onlyediting.

POST /episodes/{id}/actions/{action} body `{revision,...fields}` -> Episode:
- submit: client draft; title/brand/source/duration/prohibitedClaims (can be "None") +rights required ->submitted.
- ready: editor submitted; `{sourceUsable:true,scopeConfirmed:true,editorQualified:true,paymentPathConfirmed:true}` ->editing; records readyAt/dueAt +7 calendar days. These are recorded operator attestations, not automated payment checks.
- send: editor editing; all3checked and complete +chapters ->review; freeze snapshot.
- approve: client review; `{version,clipId,read:true}` pending only; all3->approved.
- changes: client review; `{version,clipId,feedback}` nonempty ->statuschanges.
- revise: editor review/approved/delivered; `{reason}` required; version++, all3draft/checkedfalse, clearcurrentapprovals; previous assets retainoldversion; phaseediting. Ready/due maintained; extra revision reason recorded, never auto-invoice.
- reopen: client or editor nonaccepted; `{reason}` required; version++,phase draft,rightsfalse,readyAt/dueAtnull, resetclips/review; previous assets retainoldversion. Needs full reauthorization and ready.
- deliver: editor approved; current3video+3subtitle+project allchecked, chapters nonempty, gated by server validates actual files ->delivered.
- accept: client delivered; `{read:true}` ->accepted, records receipt. Noautomaticacceptance.

## Files/exports

POST /episodes/{id}/assets multipart fields `file,revision,kind,clipId?` -> Episode. source allowed client draft/submitted, editor submitted/editing; video/subtitle/project editor approved only. Uploads100MB default limit, streaming; kind source accepts MP4/MOV/WebM validated media; video MP4 contains video/audio tracks,9:16 (tolerance2%), actual duration30–90s (0.01s numerical tolerance), duration matches approved clip +/-1s. subtitle UTF8 SRT syntax/monotonic nonoverlap/maxend validated; project UTF8 .json/.txt editable review material (not executable archive). Replacing samekind+clip+version removesoldcurrentasset and clearsQC. Source optional: permitted existing source reference can be externally delivered; ready operator explicitly verifies.
POST /episodes/{id}/assets/{assetId}/check `{revision,checked:true}` editor approved currentversion. ReturnsEpisode.
GET /episodes/{id}/assets/{assetId}/download -> authenticated file; client may download source ownorder; finalasset only currentapproved/delivered/accepted (old historical downloads editor only). no publicURL or querytoken.
GET /episodes/{id}/export -> JSON review manifest inclmissingassets,state/currentversion; NOT automatically delivered.
GET /episodes/{id}/copy -> plain UTF8 text of approved currentclipcopy/chapters (allpermitted phases, label draftwhen applicable).

## Client rules

Always replace detail from mutation response. Busydisable duplicate submits. Preserve unsaved forms when requestfails. 409 present reload option. No silent optimisticapproval. Token stored sessionStorage web, SecureStore native. Use authenticated fetch+blob for browser downloads; native FileSystem withAuthorization. No arbitrary role override.
