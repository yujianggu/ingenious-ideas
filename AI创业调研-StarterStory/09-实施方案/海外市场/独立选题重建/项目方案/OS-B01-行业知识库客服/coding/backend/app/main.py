import os, json, sqlite3, time, threading
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .store import *

def create_app(data_dir=None):
    app=FastAPI(title='B01 · 行业知识库客服'); s=Store(data_dir or os.getenv('B01_DATA_DIR',str(Path(__file__).resolve().parents[1]/'data'))); app.state.store=s
    from .products.gateway import install
    install(app, s)
    app.add_middleware(CORSMiddleware,allow_origins=os.getenv('B01_CORS_ORIGINS','http://localhost:5111,http://localhost:8111,http://127.0.0.1:5111,http://127.0.0.1:8111').split(','),allow_methods=['GET','POST','PUT','OPTIONS'],allow_headers=['Authorization','Content-Type'])
    attempts=defaultdict(list); rate_lock=threading.Lock()
    @app.middleware('http')
    async def limits(request:Request,call_next):
        length=request.headers.get('content-length','0')
        try: size=int(length)
        except ValueError: return JSONResponse({'detail':'Invalid content length'},400)
        maximum=32*1024*1024 if request.url.path.startswith('/api/products/') else 1024*1024
        if size<0: return JSONResponse({'detail':'Invalid content length'},400)
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
    return app

app=create_app()
