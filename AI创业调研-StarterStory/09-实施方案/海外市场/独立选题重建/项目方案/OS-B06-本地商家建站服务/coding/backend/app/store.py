import sqlite3, json, secrets, hashlib, hmac
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

def now(): return datetime.now(timezone.utc).isoformat()
def later(days=7): return (datetime.now(timezone.utc)+timedelta(days=days)).isoformat()
def ident(): return secrets.token_hex(16)
def fail(code, message): raise HTTPException(code, message)
def require(ok, message='Invalid request', code=422):
    if not ok: fail(code,message)
def text(value): return isinstance(value,str) and bool(value.strip())
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def password(value, salt=None):
    require(isinstance(value,str) and 10<=len(value)<=1024,'Password must contain 10–1024 characters')
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',value.encode(),bytes.fromhex(salt),600000).hex()
def verify(value, saved):
    try: return hmac.compare_digest(password(value,saved.split(':')[0]),saved)
    except (ValueError,HTTPException): return False

class Store:
    def __init__(self, root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.root.chmod(0o700)
        self.path=self.root/'project.sqlite3'
        with self.tx() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,password TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT,expires TEXT);''')
        self.path.chmod(0o600)
    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path,timeout=30); db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException: db.rollback(); raise
        finally: db.close()
    def user(self,db,authorization):
        require(authorization and authorization.startswith('Bearer '),'Authentication required',401)
        row=db.execute('SELECT u.data FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',(digest(authorization[7:]),now())).fetchone()
        require(row is not None,'Session expired or invalid',401)
        return json.loads(row['data'])
