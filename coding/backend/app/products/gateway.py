"""Private product records with transactional revisions and bounded public sites."""
import copy
import importlib
import json
import re
import threading
import time
from collections import defaultdict
from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse, Response
from ..store import ident, now, require

CODES = ('B01', 'B06', 'B09', 'C04', 'C14', 'C15', 'C17')
MAX_RECORD_BYTES = 12 * 1024 * 1024


def module(code):
    require(code in CODES, 'Product not found', 404)
    return importlib.import_module('.' + code.lower(), __package__)


def install(app, store):
    with store.tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS product_records(id TEXT PRIMARY KEY, code TEXT NOT NULL, owner TEXT NOT NULL, data TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS products_by_owner ON product_records(owner,code)')
    router = APIRouter()

    def owned(db, code, rid, user):
        row = db.execute('SELECT data FROM product_records WHERE id=? AND code=? AND owner=?', (rid, code, user['id'])).fetchone()
        require(row is not None, 'Record not found', 404)
        return json.loads(row['data'])

    def check_revision(record, body):
        require(type(body.get('revision')) is int and body['revision'] == record['revision'], 'This record changed. Reload the latest version before retrying.', 409)

    def validate_unicode(value):
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, UnicodeError, RecursionError):
            require(False, 'Use valid Unicode text and finite JSON values')

    def serialize(record):
        validate_unicode(record)
        require(isinstance(record, dict) and isinstance(record.get('title'), str) and 0 < len(record['title'].strip()) <= 500, 'A title of 1–500 characters is required')
        value = json.dumps(record, ensure_ascii=False, allow_nan=False)
        require(len(value.encode()) <= MAX_RECORD_BYTES, 'Record exceeds 12 MiB. Remove unused attachments or split the collection.', 413)
        return value

    @router.get('/api/products/{code}/records')
    def records(code: str, authorization: str | None = Header(None)):
        require(code in CODES, 'Product not found', 404)
        with store.tx() as db:
            user = store.user(db, authorization)
            return {'records': [json.loads(r['data']) for r in db.execute('SELECT data FROM product_records WHERE owner=? AND code=? ORDER BY rowid DESC', (user['id'], code))]}

    @router.post('/api/products/{code}/records')
    def create(code: str, body: dict, authorization: str | None = Header(None)):
        validate_unicode(body)
        domain = module(code)
        with store.tx() as db:
            user = store.user(db, authorization)
            count = db.execute('SELECT COUNT(*) FROM product_records WHERE owner=? AND code=?', (user['id'], code)).fetchone()[0]
            require(count < 100, 'Limit reached: 100 records per project. Export and delete unused records.')
            record = domain.create(body)
            record.update(id=ident(), code=code, revision=1, createdAt=now(), updatedAt=now())
            db.execute('INSERT INTO product_records VALUES(?,?,?,?)', (record['id'], code, user['id'], serialize(record)))
            return record

    @router.get('/api/products/{code}/records/{rid}')
    def detail(code: str, rid: str, authorization: str | None = Header(None)):
        with store.tx() as db:
            return owned(db, code, rid, store.user(db, authorization))

    @router.post('/api/products/{code}/records/{rid}/actions/{action}')
    def action(code: str, rid: str, action: str, body: dict, authorization: str | None = Header(None)):
        validate_unicode(body)
        domain = module(code)
        with store.tx() as db:
            user = store.user(db, authorization)
            record = owned(db, code, rid, user)
            check_revision(record, body)
            updated = domain.act(copy.deepcopy(record), action, body)
            # Domain handlers cannot change identity, ownership, or revision semantics.
            updated.update(id=rid, code=code, revision=record['revision']+1, createdAt=record['createdAt'], updatedAt=now())
            db.execute('UPDATE product_records SET data=? WHERE id=?', (serialize(updated), rid))
            return updated

    @router.post('/api/products/{code}/records/{rid}/delete')
    def delete(code: str, rid: str, body: dict, authorization: str | None = Header(None)):
        with store.tx() as db:
            user = store.user(db, authorization)
            record = owned(db, code, rid, user)
            check_revision(record, body)
            require(body.get('confirm') is True, 'Deletion confirmation required')
            db.execute('DELETE FROM product_records WHERE id=?', (rid,))
            return {'ok': True}

    @router.get('/api/products/{code}/records/{rid}/export/{format}')
    def export(code: str, rid: str, format: str, authorization: str | None = Header(None)):
        with store.tx() as db:
            record = owned(db, code, rid, store.user(db, authorization))
        if format == 'json':
            content, mime, filename = json.dumps(record, ensure_ascii=False).encode(), 'application/json', f'{code}-{rid}.json'
        else:
            content, mime, filename = module(code).export(record, format)
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)[:160] or 'download'
        return Response(content, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{safe_name}"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    def public_record(db, rid):
        row = db.execute("SELECT data FROM product_records WHERE id=? AND code='B06'", (rid,)).fetchone()
        require(row is not None, 'Site not found', 404)
        return json.loads(row['data'])

    @router.get('/api/public/sites/{rid}', response_class=HTMLResponse)
    def public_site(rid: str):
        with store.tx() as db:
            record = public_record(db, rid)
        html = module('B06').public_view(record)
        return HTMLResponse(html, headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        })

    attempts = defaultdict(list)
    lock = threading.Lock()

    @router.post('/api/public/sites/{rid}/inquiries')
    async def public_inquiry(rid: str, request: Request):
        key = request.client.host if request.client else 'unknown'
        tick = time.monotonic()
        with lock:
            for ip in list(attempts):
                attempts[ip] = [t for t in attempts[ip] if t > tick-60]
                if not attempts[ip]: del attempts[ip]
            require(len(attempts) < 10000 or key in attempts, 'Please retry later', 429)
            require(len(attempts[key]) < 10, 'Too many inquiries. Please wait a minute.', 429)
            attempts[key].append(tick)
        is_json = request.headers.get('content-type', '').split(';')[0] == 'application/json'
        try:
            body = await request.json() if is_json else dict(await request.form())
        except (ValueError, UnicodeError):
            require(False, 'Invalid inquiry')
        require(isinstance(body, dict), 'Invalid inquiry')
        validate_unicode(body)
        with store.tx() as db:
            record = public_record(db, rid)
            updated = module('B06').public_action(copy.deepcopy(record), body)
            updated.update(id=rid, code='B06', revision=record['revision']+1, createdAt=record['createdAt'], updatedAt=now())
            db.execute('UPDATE product_records SET data=? WHERE id=?', (serialize(updated), rid))
        if is_json: return {'ok': True, 'message': 'Inquiry received. The business will review it.'}
        return HTMLResponse('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Inquiry received</title><main><h1>Thank you</h1><p>Your inquiry was received. The business will review it.</p></main></html>', headers={'Cache-Control': 'no-store'})

    app.include_router(router)
