import json
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.main import create_app
from app.store import digest


def setup(tmp_path):
    app=create_app(data_dir=tmp_path); c=TestClient(app)
    editor=c.post('/api/auth/register',json=dict(name='Editor',email='e@test.local',password='secure-password',workspaceName='Studio')).json(); eh={'Authorization':'Bearer '+editor['token']}
    inv=c.post('/api/invites',headers=eh,json={'email':'client@test.local'}).json()
    client=c.post('/api/auth/accept-invite',json=dict(token=inv['token'],name='Client',password='secure-password')).json(); ch={'Authorization':'Bearer '+client['token']}
    e=c.post('/api/episodes',headers=ch,json=dict(title='Title',brand='Brand',source='Source',duration=120,prohibitedClaims='None')).json()
    return app,c,eh,ch,e


def test_bad_inputs_session_expiry_and_foreign_workspace(tmp_path):
    app,c,eh,ch,e=setup(tmp_path)
    for email in (None,[],123):
        assert c.post('/api/auth/register',json=dict(name='Name',email=email,password='secure-password',workspaceName='Studio')).status_code==422
    assert c.post('/api/episodes',headers=eh,json=dict(title='Title',brand='Brand',source='Source',duration=120,clientId=[])).status_code==422
    other=c.post('/api/auth/register',json=dict(name='Other',email='other@test.local',password='secure-password',workspaceName='Other')).json()
    oh={'Authorization':'Bearer '+other['token']}
    assert c.get('/api/episodes/'+e['id'],headers=oh).status_code==404
    assert c.post('/api/episodes/'+e['id']+'/assets',headers=oh,data={'revision':1,'kind':'source'},files={'file':('x.mp4',b'bad')}).status_code==404
    with app.state.store.tx() as db: db.execute('UPDATE sessions SET expires=? WHERE token=?',('2000-01-01',digest(ch['Authorization'][7:])))
    assert c.get('/api/auth/me',headers=ch).status_code==401


def test_simultaneous_revision_and_reopen_invalidation(tmp_path):
    app,c,eh,ch,e=setup(tmp_path); url='/api/episodes/'+e['id']
    def write(_): return c.put(url+'/brief',headers=ch,json={**e,'rights':True}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(write,range(2)))
    assert sorted(results)==[200,409]
    e=c.get(url,headers=ch).json(); assert e['revision']==2
    e=c.post(url+'/actions/submit',headers=ch,json={'revision':e['revision']}).json()
    e=c.post(url+'/actions/ready',headers=eh,json=dict(revision=e['revision'],sourceUsable=True,scopeConfirmed=True,editorQualified=True,paymentPathConfirmed=True)).json()
    from datetime import datetime, timedelta
    assert datetime.fromisoformat(e['dueAt'])-datetime.fromisoformat(e['readyAt'])==timedelta(days=7)
    e=c.post(url+'/actions/reopen',headers=ch,json=dict(revision=e['revision'],reason='Replace source')).json()
    assert e['phase']=='draft' and e['version']==2 and e['readyAt'] is None and e['dueAt'] is None and not e['rights']
    assert all(x['status']=='draft' and not x['checked'] for x in e['clips'])
    assert c.post(url+'/actions/submit',headers=ch,json={'revision':e['revision']}).status_code==422
