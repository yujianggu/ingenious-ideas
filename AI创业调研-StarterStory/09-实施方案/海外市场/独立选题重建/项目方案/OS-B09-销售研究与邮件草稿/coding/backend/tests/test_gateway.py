import sys
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.main import create_app


def session(client, email):
    res = client.post('/api/auth/register', json={'name': 'Owner', 'workspaceName': 'Projects', 'email': email, 'password': 'a-safe-password-567'})
    assert res.status_code == 200
    return {'Authorization': 'Bearer '+res.json()['token']}


def test_product_isolation_revision_export_delete(tmp_path, monkeypatch):
    def act(r, a, b):
        r['title'] = b['title']; return r
    module = SimpleNamespace(create=lambda b: {'title': b['title'], 'items': []}, act=act, export=lambda r, f: (r['title'].encode(), 'text/plain', 'record.txt'))
    monkeypatch.setitem(sys.modules, 'app.products.b09', module)
    client = TestClient(create_app(tmp_path))
    alice = session(client, 'alice@example.com'); bob = session(client, 'bob@example.com')
    path = '/api/products/B09/records'
    assert client.get(path).status_code == 401
    r = client.post(path, json={'title': 'Private knowledge'}, headers=alice)
    assert r.status_code == 200, r.text
    record = r.json(); url = path+'/'+record['id']
    assert client.get(path, headers=bob).json() == {'records': []}
    assert client.get(url, headers=bob).status_code == 404
    assert client.post(url+'/actions/change', json={'revision': 1, 'title': 'Stolen'}, headers=bob).status_code == 404
    assert client.get(url.replace('B09', 'OTHER'), headers=alice).status_code == 404
    assert client.post(url+'/actions/change', json={'revision': 0, 'title': 'Stale'}, headers=alice).status_code == 409
    newer = client.post(url+'/actions/change', json={'revision': 1, 'title': 'Current'}, headers=alice)
    assert newer.status_code == 200, newer.text
    assert newer.json()['revision'] == 2
    assert client.get(url+'/export/txt', headers=bob).status_code == 404
    assert client.get(url+'/export/txt', headers=alice).text == 'Current'
    backup = client.get(url+'/export/json', headers=alice)
    assert backup.json()['title'] == 'Current'
    assert backup.headers['cache-control'] == 'no-store'
    assert client.post(url+'/delete', json={'revision': 2, 'confirm': False}, headers=alice).status_code == 422
    assert client.post(url+'/delete', json={'revision': 1, 'confirm': True}, headers=alice).status_code == 409
    assert client.post(url+'/delete', json={'revision': 2, 'confirm': True}, headers=alice).status_code == 200
    assert client.get(url, headers=alice).status_code == 404


def test_gateway_protects_envelope(tmp_path, monkeypatch):
    def act(r, a, b):
        r.update(id='injected', code='B09', revision=500, title='Updated'); return r
    module = SimpleNamespace(create=lambda b: {'title': 'Owned', 'id': 'injected', 'code': 'B09', 'revision': 900}, act=act)
    monkeypatch.setitem(sys.modules, 'app.products.b09', module)
    client = TestClient(create_app(tmp_path)); auth = session(client, 'safe@example.com')
    path = '/api/products/B09/records'; record = client.post(path, json={}, headers=auth).json()
    assert record['id'] != 'injected' and record['code'] == 'B09' and record['revision'] == 1
    url = path+'/'+record['id']
    newer = client.post(url+'/actions/edit', json={'revision': 1}, headers=auth).json()
    assert newer['id'] == record['id'] and newer['code'] == 'B09' and newer['revision'] == 2
    assert client.get('/api/products/NOPE/records', headers=auth).status_code == 404


def test_gateway_rejects_invalid_unicode_and_preserves_large_roundtrip_budget(tmp_path, monkeypatch):
    module = SimpleNamespace(create=lambda b: {'title': b['title'], 'content': b.get('content', '')}, act=lambda r, a, b: {**r, 'content': b['content']})
    monkeypatch.setitem(sys.modules, 'app.products.b09', module)
    client = TestClient(create_app(tmp_path)); auth = session(client, 'unicode@example.com')
    path = '/api/products/B09/records'
    invalid = client.post(path, content='{"title":"\\ud800"}', headers={**auth, 'Content-Type':'application/json'})
    assert invalid.status_code == 422
    record = client.post(path, json={'title':'Large import','content':'x'*(2*1024*1024)}, headers=auth)
    assert record.status_code == 200, record.text[:200]
    rid = record.json()['id']
    exported = client.get(f'{path}/{rid}/export/json', headers=auth)
    assert len(exported.json()['content']) == 2*1024*1024
