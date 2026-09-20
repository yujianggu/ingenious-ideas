import os, json, sqlite3, copy, time, threading
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI, Header, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from .store import *
from .media import validate

def create_app(data_dir=None):
    app=FastAPI(title='Overseas Product Studio'); s=Store(data_dir or os.getenv('EPISODE_DATA_DIR','data')); app.state.store=s
    from .products.gateway import install
    install(app, s)
    app.add_middleware(CORSMiddleware,allow_origins=os.getenv('EPISODE_CORS_ORIGINS','http://localhost:5173,http://localhost:8081').split(','),allow_methods=['GET','POST','PUT','OPTIONS'],allow_headers=['Authorization','Content-Type'])
    attempts=defaultdict(list); rate_lock=threading.Lock()
    @app.middleware('http')
    async def limits(request:Request,call_next):
        length=request.headers.get('content-length','0')
        try: size=int(length)
        except ValueError: return JSONResponse({'detail':'Invalid content length'},400)
        maximum=int(os.getenv('EPISODE_MAX_UPLOAD_BYTES',str(100*1024*1024)))+1024*1024 if '/assets' in request.url.path else (32*1024*1024 if request.url.path.startswith('/api/products/') else 1024*1024)
        if size>maximum: return JSONResponse({'detail':'Request too large'},413)
        if request.url.path.startswith('/api/auth/') and request.method=='POST':
            key=request.client.host if request.client else 'unknown'; t=time.monotonic()
            with rate_lock:
                attempts[key]=[x for x in attempts[key] if x>t-60]
                if len(attempts[key])>=30: return JSONResponse({'detail':'Too many authentication attempts; retry in one minute'},429)
                attempts[key].append(t)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    @app.get('/api/health')
    def health(): return {'ok':True}
    def session(db,u):
        token=secrets.token_urlsafe(32); expires=later(7)
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),u['id'],expires)); return dict(token=token,expiresAt=expires,user=u)
    def newuser(db,b,workspace,urole):
        require(text(b.get('name')) and len(b['name'])<=200,'Name required')
        require(isinstance(b.get('email'),str),'Valid email required'); email=b['email'].strip().lower(); require('@' in email and len(email)<=254,'Valid email required')
        hashed=password(b.get('password')); u=dict(id=ident(),name=b['name'].strip(),email=email,role=urole,workspaceId=workspace)
        try: db.execute('INSERT INTO users VALUES(?,?,?,?)',(u['id'],email,hashed,json.dumps(u)))
        except sqlite3.IntegrityError: fail(422,'Email already registered')
        return u
    @app.post('/api/auth/register')
    def register(b:dict):
        require(text(b.get('workspaceName')),'Workspace name required')
        with s.tx() as db: return session(db,newuser(db,b,ident(),'editor'))
    @app.post('/api/auth/login')
    def login(b:dict):
        with s.tx() as db:
            row=db.execute('SELECT * FROM users WHERE email=?',(str(b.get('email','')).strip().lower(),)).fetchone()
            require(row and verify(b.get('password',''),row['password']),'Invalid email or password',401)
            return session(db,json.loads(row['data']))
    @app.get('/api/auth/me')
    def me(authorization:str|None=Header(None)):
        with s.tx() as db: return s.user(db,authorization)
    @app.post('/api/auth/logout')
    def logout(authorization:str|None=Header(None)):
        with s.tx() as db:
            s.user(db,authorization); db.execute('DELETE FROM sessions WHERE token=?',(digest(authorization[7:]),)); return {'ok':True}
    @app.post('/api/invites')
    def invite(b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); role(u,'editor'); email=str(b.get('email','')).strip().lower(); require('@' in email and len(email)<=254,'Valid email required')
            require(not db.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone(),'Email already registered')
            token=secrets.token_urlsafe(32); expires=later(3); db.execute('INSERT INTO invites VALUES(?,?,?,?)',(digest(token),email,u['workspaceId'],expires)); return dict(token=token,email=email,expiresAt=expires)
    @app.post('/api/auth/accept-invite')
    def join(b:dict):
        with s.tx() as db:
            token=digest(str(b.get('token',''))); inv=db.execute('SELECT * FROM invites WHERE token=? AND expires>?',(token,now())).fetchone(); require(inv,'Invite expired or already used')
            u=newuser(db,{**b,'email':inv['email']},inv['workspace'],'client'); db.execute('DELETE FROM invites WHERE token=?',(token,)); return session(db,u)
    @app.get('/api/members')
    def members(authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); role(u,'editor'); return {'members':[m for r in db.execute('SELECT data FROM users') if (m:=json.loads(r['data']))['workspaceId']==u['workspaceId']]}
    @app.get('/api/episodes')
    def episodes(authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); return {'episodes':[json.loads(r['data']) for r in db.execute('SELECT * FROM episodes WHERE workspace=? ORDER BY rowid DESC',(u['workspaceId'],)) if u['role']=='editor' or r['client']==u['id']]}
    def brief(b):
        for key in ('title','brand','source'): require(text(b.get(key)) and len(b[key])<=10000,f'{key} required')
        require(type(b.get('duration')) is int and 90<=b['duration']<=3600,'Duration must be 90–3600 seconds')
        for key in ('notes','glossary','prohibitedClaims'): require(isinstance(b.get(key,''),str) and len(b.get(key,''))<=50000,f'Invalid {key}')
        return {key:b.get(key,'') for key in ('title','brand','source','duration','notes','glossary','prohibitedClaims')}
    @app.post('/api/episodes')
    def create(b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); client=u['id'] if u['role']=='client' else b.get('clientId')
            require(isinstance(client,str),'Workspace client required'); row=db.execute('SELECT data FROM users WHERE id=?',(client,)).fetchone(); require(row,'Workspace client required'); target=json.loads(row['data']); require(target['role']=='client' and target['workspaceId']==u['workspaceId'],'Workspace client required')
            e={**brief(b), 'id':ident(),'clientId':client,'rights':False,'phase':'draft','version':1,'revision':1,'createdAt':now(),'updatedAt':now(),'readyAt':None,'dueAt':None,'clips':blankclips(),'chapters':'','history':[],'events':[],'assets':[]}
            db.execute('INSERT INTO episodes VALUES(?,?,?,?)',(e['id'],u['workspaceId'],client,json.dumps(e))); return e
    @app.get('/api/episodes/{eid}')
    def get(eid:str,authorization:str|None=Header(None)):
        with s.tx() as db: return s.episode(db,eid,s.user(db,authorization))
    @app.put('/api/episodes/{eid}/brief')
    def editbrief(eid:str,b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); role(u,'client'); revision(e,b); phase(e,'draft'); e.update(brief(b)); require(type(b.get('rights')) is bool,'Rights confirmation required'); e['rights']=b['rights']; return s.save(db,e,u,'brief updated')
    @app.put('/api/episodes/{eid}/clips/{cid}')
    def editclip(eid:str,cid:str,b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); role(u,'editor'); revision(e,b); phase(e,'editing'); clip=next((c for c in e['clips'] if c['id']==cid),None); require(clip,'Clip not found',404)
            for k in ('title','quote','context','post'): require(isinstance(b.get(k),str) and len(b[k])<=50000,f'Invalid {k}')
            require(type(b.get('start')) is int and type(b.get('end')) is int and b['start']>=0 and b['end']<=e['duration'] and 30<=b['end']-b['start']<=90,'Clip must be 30–90 seconds within source'); require(type(b.get('checked')) is bool,'Checked must be boolean')
            clip.update({k:b[k] for k in ('title','quote','context','post','start','end','checked')}); return s.save(db,e,u,'clip updated')
    @app.put('/api/episodes/{eid}/chapters')
    def chapters(eid:str,b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); role(u,'editor'); revision(e,b); phase(e,'editing'); require(isinstance(b.get('chapters'),str) and len(b['chapters'])<=50000,'Invalid chapters'); e['chapters']=b['chapters']; return s.save(db,e,u,'chapters updated')
    @app.post('/api/episodes/{eid}/actions/{action}')
    def action(eid:str,action:str,b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); revision(e,b)
            if action=='submit':
                role(u,'client'); phase(e,'draft'); brief(e); require(text(e['prohibitedClaims']) and e['rights'],'Rights and prohibited claims required'); e['phase']='submitted'
            elif action=='ready':
                role(u,'editor'); phase(e,'submitted'); require(all(b.get(k) is True for k in ('sourceUsable','scopeConfirmed','editorQualified','paymentPathConfirmed')),'All four start checks required'); ready=now(); e.update(phase='editing',readyAt=ready,dueAt=(datetime.fromisoformat(ready)+timedelta(days=7)).isoformat()); e['startChecks']={k:True for k in ('sourceUsable','scopeConfirmed','editorQualified','paymentPathConfirmed')}
            elif action=='send':
                role(u,'editor'); phase(e,'editing'); require(text(e['chapters']) and all(c['checked'] and all(text(c[k]) for k in ('title','quote','context','post')) for c in e['clips']),'Three complete checked clips and chapters required')
                for c in e['clips']:
                    require(0<=c['start']<c['end']<=e['duration'] and 30<=c['end']-c['start']<=90,'Clip range invalid'); c['status']='pending'; c['feedback']=''
                e['phase']='review'; e['history'].append(dict(version=e['version'],sentAt=now(),title=e['title'],source=e['source'],duration=e['duration'],chapters=e['chapters'],clips=copy.deepcopy(e['clips']),decisions=[]))
            elif action in ('approve','changes'):
                role(u,'client'); phase(e,'review'); require(type(b.get('version')) is int and b['version']==e['version'],'Review version changed',409); c=next((c for c in e['clips'] if c['id']==b.get('clipId')),None); require(c,'Clip not found',404); require(c['status']=='pending','Clip already reviewed')
                require(b.get('read') is True if action=='approve' else text(b.get('feedback')),'Read confirmation or feedback required'); c['status']='approved' if action=='approve' else 'changes'; c['feedback']=b.get('feedback','') if action=='changes' else ''
                e['history'][-1]['decisions'].append(dict(clipId=c['id'],status=c['status'],feedback=c['feedback'],actorId=u['id'],time=now()))
                if all(c['status']=='approved' for c in e['clips']): e['phase']='approved'
            elif action in ('revise','reopen'):
                require(text(b.get('reason')),'Reason required')
                if action=='revise': role(u,'editor'); phase(e,'review','approved','delivered')
                else: require(e['phase']!='accepted','Accepted episodes cannot reopen')
                e['version']+=1; e['phase']='editing' if action=='revise' else 'draft'
                if action=='reopen': e.update(rights=False,readyAt=None,dueAt=None,clips=blankclips(),chapters=''); e.pop('startChecks',None)
                for c in e['clips']: c.update(status='draft',checked=False,feedback='')
            elif action=='deliver':
                role(u,'editor'); phase(e,'approved'); require(not missing(e),'All current deliverables must be uploaded and checked')
                for a in e['assets']:
                    if a['version']==e['version'] and a['kind']!='source':
                        row=db.execute('SELECT path FROM files WHERE id=?',(a['id'],)).fetchone(); require(row,'File missing'); validate(s.files/row['path'],a['kind'],a['filename'],next((c for c in e['clips'] if c['id']==a['clipId']),None))
                e['phase']='delivered'
            elif action=='accept': role(u,'client'); phase(e,'delivered'); require(b.get('read') is True,'Receipt confirmation required'); e.update(phase='accepted',acceptedAt=now())
            else: fail(404,'Unknown action')
            return s.save(db,e,u,action+(': '+b['reason'] if action in ('revise','reopen') else ''))
    def missing(e):
        required=[('project',None)]+[(kind,c['id']) for c in e['clips'] for kind in ('video','subtitle')]
        return [f'{kind}:{cid or "episode"}' for kind,cid in required if not any(a['version']==e['version'] and a['kind']==kind and a['clipId']==cid and a['checked'] for a in e['assets'])]+([] if text(e['chapters']) else ['chapters'])
    @app.post('/api/episodes/{eid}/assets')
    def upload(eid:str,file:UploadFile=File(...),revision:int=Form(...),kind:str=Form(...),clipId:str|None=Form(None),authorization:str|None=Header(None)):
        # Write/validate privately, then recheck authorization/state/revision under the same transaction as replacement.
        aid=ident(); path=s.files/aid; oldpaths=[]
        try:
            with s.tx() as db:
                u=s.user(db,authorization); e=s.episode(db,eid,u); globals()['revision'](e,{'revision':revision})
                require(kind in ('source','video','subtitle','project'),'Invalid asset kind')
                if kind=='source': phase(e,*(['draft','submitted'] if u['role']=='client' else ['submitted','editing'])); require(not clipId,'Source has no clip ID')
                else: role(u,'editor'); phase(e,'approved')
                clip=next((c for c in e['clips'] if c['id']==clipId),None)
                if kind in ('video','subtitle'): require(clip,'Clip ID required')
                if kind=='project': require(not clipId,'Project has no clip ID')
            filename=Path((file.filename or 'upload').replace('\\','/')).name; require(len(filename)<=200,'Filename too long'); size=0
            with path.open('xb') as out:
                path.chmod(0o600)
                while chunk:=file.file.read(1024*1024):
                    size+=len(chunk); require(size<=int(os.getenv('EPISODE_MAX_UPLOAD_BYTES',str(100*1024*1024))),'File exceeds upload limit',413); out.write(chunk)
            require(size>0,'File cannot be empty'); validate(path,kind,filename,clip)
            with s.tx() as db:
                u=s.user(db,authorization); e=s.episode(db,eid,u); globals()['revision'](e,{'revision':revision})
                for a in list(e['assets']):
                    if (a['kind'],a['clipId'],a['version'])==(kind,clipId,e['version']):
                        row=db.execute('SELECT path FROM files WHERE id=?',(a['id'],)).fetchone()
                        if row: oldpaths.append(s.files/row['path'])
                        db.execute('DELETE FROM files WHERE id=?',(a['id'],)); e['assets'].remove(a)
                content={'video':'video/mp4','subtitle':'application/x-subrip','project':'text/plain','source':{'.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm'}.get(Path(filename).suffix.lower(),'application/octet-stream')}[kind]
                e['assets'].append(dict(id=aid,kind=kind,clipId=clipId,version=e['version'],filename=filename,size=size,contentType=content,checked=False,createdAt=now())); db.execute('INSERT INTO files VALUES(?,?)',(aid,aid)); result=s.save(db,e,u,'asset uploaded')
        except BaseException:
            path.unlink(missing_ok=True); raise
        finally: file.file.close()
        for p in oldpaths: p.unlink(missing_ok=True)
        return result
    @app.post('/api/episodes/{eid}/assets/{aid}/check')
    def check(eid:str,aid:str,b:dict,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); role(u,'editor'); revision(e,b); phase(e,'approved'); a=next((a for a in e['assets'] if a['id']==aid),None); require(a,'Asset not found',404); require(a['version']==e['version'] and a['kind']!='source','Only current final assets can be checked'); require(b.get('checked') is True,'Explicit QC confirmation required'); a['checked']=True; return s.save(db,e,u,'asset quality checked')
    @app.get('/api/episodes/{eid}/assets/{aid}/download')
    def download(eid:str,aid:str,authorization:str|None=Header(None)):
        with s.tx() as db:
            u=s.user(db,authorization); e=s.episode(db,eid,u); a=next((a for a in e['assets'] if a['id']==aid),None); require(a,'Asset not found',404)
            if u['role']=='client' and a['kind']!='source': require(a['version']==e['version'] and e['phase'] in ('approved','delivered','accepted'),'Asset not available',403)
            row=db.execute('SELECT path FROM files WHERE id=?',(aid,)).fetchone(); require(row and (s.files/row['path']).is_file(),'File unavailable',404)
            handle=(s.files/row['path']).open('rb')
            def chunks():
                try:
                    while chunk:=handle.read(65536): yield chunk
                finally: handle.close()
            from urllib.parse import quote
            return StreamingResponse(chunks(),media_type=a['contentType'],headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(a['filename']),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.get('/api/episodes/{eid}/export')
    def export(eid:str,authorization:str|None=Header(None)):
        with s.tx() as db:
            e=s.episode(db,eid,s.user(db,authorization)); return {'episode':e,'state':e['phase'],'currentVersion':e['version'],'missingAssets':missing(e)}
    @app.get('/api/episodes/{eid}/copy',response_class=PlainTextResponse)
    def copytext(eid:str,authorization:str|None=Header(None)):
        with s.tx() as db:
            e=s.episode(db,eid,s.user(db,authorization)); label='APPROVED' if e['phase'] in ('approved','delivered','accepted') else 'DRAFT'; return '\n\n'.join([f'{label} · {e["title"]} · v{e["version"]}',e['chapters']]+[f'{c["title"]}\n{c["post"]}' for c in e['clips']])
    return app

app=create_app()
