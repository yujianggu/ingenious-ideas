from fastapi.testclient import TestClient
from app.main import create_app

def test_auth_persistence_and_isolation(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as c:
        r=c.post('/api/auth/register',json=dict(name='Editor',email='editor@test.local',password='secure-pass123',workspaceName='Studio'))
        assert r.status_code==200
        token=r.json()['token']; h={'Authorization':'Bearer '+token}
        clients=[]
        for n in ('one','two'):
            inv=c.post('/api/invites',headers=h,json={'email':n+'@test.local'}).json()
            r=c.post('/api/auth/accept-invite',json={'token':inv['token'],'name':n,'password':'secure-pass123'})
            assert r.status_code==200
            clients.append(r.json())
            assert c.post('/api/auth/accept-invite',json={'token':inv['token'],'name':n,'password':'secure-pass123'}).status_code==422
        a={'Authorization':'Bearer '+clients[0]['token']}; b={'Authorization':'Bearer '+clients[1]['token']}
        e=c.post('/api/episodes',headers=a,json={'title':'Episode','brand':'Acme','source':'https://source.example/file','duration':180,'prohibitedClaims':'None'}).json()
        assert c.get('/api/episodes/'+e['id'],headers=b).status_code==404
        assert c.get('/api/episodes',headers=b).json()=={'episodes':[]}
        url='/api/episodes/'+e['id']
        assert c.post(url+'/actions/submit',headers=a,json={'revision':1}).status_code==422
        e=c.put(url+'/brief',headers=a,json={**e,'rights':True}).json()
        assert c.put(url+'/brief',headers=a,json={**e,'revision':1}).status_code==409
        e=c.post(url+'/actions/submit',headers=a,json={'revision':e['revision']}).json()
        assert e['phase']=='submitted'
        assert c.post(url+'/actions/ready',headers=a,json={'revision':e['revision']}).status_code==403
        e=c.post(url+'/actions/ready',headers=h,json={'revision':e['revision'],'sourceUsable':True,'scopeConfirmed':True,'editorQualified':True,'paymentPathConfirmed':True}).json()
        assert e['phase']=='editing'
        assert c.post(url+'/actions/send',headers=h,json={'revision':e['revision']}).status_code==422
        for i in range(1,4):
            e=c.put(url+f'/clips/clip-{i}',headers=h,json={'revision':e['revision'],'title':'Clip','start':0,'end':30,'quote':'Quote','context':'Context','post':'Post','checked':True}).json()
        e=c.put(url+'/chapters',headers=h,json={'revision':e['revision'],'chapters':'00:00 Introduction'}).json()
        e=c.post(url+'/actions/send',headers=h,json={'revision':e['revision']}).json()
        assert e['phase']=='review' and len(e['history'])==1
        assert c.post(url+'/actions/approve',headers=a,json={'revision':e['revision'],'version':0,'clipId':'clip-1','read':True}).status_code==409
        for i in range(1,4):
            e=c.post(url+'/actions/approve',headers=a,json={'revision':e['revision'],'version':1,'clipId':f'clip-{i}','read':True}).json()
        assert e['phase']=='approved'
        assert c.post(url+'/actions/deliver',headers=h,json={'revision':e['revision']}).status_code==422
        assert c.post(url+'/actions/revise',headers=h,json={'revision':e['revision'],'reason':''}).status_code==422
        assert c.post(url+'/assets',headers=h,data={'revision':e['revision'],'kind':'video','clipId':'clip-1'},files={'file':('fake.mp4',b'not media','video/mp4')}).status_code==422
        assert c.post(url+'/assets',headers=h,data={'revision':e['revision'],'kind':'subtitle','clipId':'clip-1'},files={'file':('fake.srt',b'bad','text/plain')}).status_code==422
        c.post('/api/auth/logout',headers=h)
        assert c.get('/api/auth/me',headers=h).status_code==401
    with TestClient(create_app(data_dir=tmp_path)) as c:
        assert c.get(url,headers=a).json()['phase']=='approved'
