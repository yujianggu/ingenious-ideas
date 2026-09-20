from fastapi.testclient import TestClient
from app.main import create_app
from app.products.gateway import CODES

CODE = 'B09'
CREATE, ACTION, BODY = ({'title': 'Research', 'companyName': 'Acme', 'website': 'https://example.com', 'icp': 'logistics', 'offer': 'Planning'}, 'add-source', {'title': 'Company', 'url': 'https://example.com/about', 'content': 'Acme offers logistics.'})

def login(client, email):
    response = client.post('/api/auth/register', json={'name':'Owner','email':email,'password':'safe-password-123','workspaceName':'Private'})
    assert response.status_code == 200, response.text
    return {'Authorization':'Bearer '+response.json()['token']}

def test_standalone_domain_crud_and_sessions(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    alice = login(client, 'alice@example.com')
    bob = login(client, 'bob@example.com')
    assert CODES == (CODE,)
    base = f'/api/products/{CODE}/records'
    result = client.post(base, json=CREATE, headers=alice)
    assert result.status_code == 200, result.text
    record = result.json()
    url = base+'/'+record['id']
    assert client.get(base, headers=bob).json() == {'records':[]}
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(url+'/export/json', headers=bob).status_code == 404
    assert client.post(url+'/actions/'+ACTION, json={**BODY,'revision':1}, headers=bob).status_code == 404
    assert client.post(url+'/delete', json={'confirm':True,'revision':1}, headers=bob).status_code == 404
    changed = client.post(url+'/actions/'+ACTION, json={**BODY,'revision':1}, headers=alice)
    assert changed.status_code == 200, changed.text
    assert changed.json()['revision'] == 2
    assert client.post(url+'/actions/'+ACTION, json={**BODY,'revision':1}, headers=alice).status_code == 409
    assert client.get(url, headers=alice).json() == changed.json()
    exported = client.get(url+'/export/json', headers=alice)
    assert exported.json() == changed.json()
    assert exported.headers['cache-control'] == 'no-store'
    # Fresh app process state reads the same private database.
    assert TestClient(create_app(tmp_path)).get(url, headers=alice).json() == changed.json()
    assert client.post(url+'/delete', json={'confirm':True,'revision':2}, headers=alice).status_code == 200
    assert client.get(url, headers=alice).status_code == 404
    assert client.get('/api/auth/me', headers=alice).status_code == 200
    assert client.post('/api/auth/logout', headers=alice).status_code == 200
    assert client.get('/api/auth/me', headers=alice).status_code == 401
    logged = client.post('/api/auth/login', json={'email':'alice@example.com','password':'safe-password-123'})
    assert logged.status_code == 200
    with app.state.store.tx() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {'users','sessions','product_records'}
    assert app.state.store.path.name == 'project.sqlite3'
    assert app.state.store.root.stat().st_mode & 0o777 == 0o700
    assert app.state.store.path.stat().st_mode & 0o777 == 0o600

def test_other_ideas_and_b04_are_absent(tmp_path):
    client = TestClient(create_app(tmp_path))
    auth = login(client, 'owner@example.com')
    for code in ('B01','B04','B06','B09','C04','C14','C15','C17','UNKNOWN'):
        if code == CODE: continue
        base = f'/api/products/{code}/records'
        for headers in ({}, auth):
            assert client.get(base, headers=headers).status_code == 404
            assert client.post(base, json={}, headers=headers).status_code == 404
            assert client.get(base+'/missing', headers=headers).status_code == 404
            assert client.get(base+'/missing/export/json', headers=headers).status_code == 404
            assert client.post(base+'/missing/delete', json={}, headers=headers).status_code == 404
            assert client.post(base+'/missing/actions/edit', json={}, headers=headers).status_code == 404
    for path in ('/api/episodes','/api/episodes/missing','/api/invites','/api/members','/api/auth/accept-invite'):
        assert client.get(path, headers=auth).status_code == 404
        assert client.post(path, json={}, headers=auth).status_code == 404
    paths = set(client.get('/openapi.json').json()['paths'])
    assert ('/api/public/sites/{rid}' in paths) == (CODE == 'B06')
    assert ('/api/public/sites/{rid}/inquiries' in paths) == (CODE == 'B06')
