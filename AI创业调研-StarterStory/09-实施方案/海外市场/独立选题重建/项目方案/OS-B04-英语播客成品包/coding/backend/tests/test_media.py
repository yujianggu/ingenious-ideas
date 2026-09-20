from fastapi.testclient import TestClient
from app.main import create_app
from app.backup import backup, restore
from media_fixture import generate
import pytest

def test_real_delivery_and_backup(tmp_path):
    data=tmp_path/'data'; c=TestClient(create_app(data_dir=data))
    def post(path,body,h=None):
        r=c.post('/api'+path,json=body,headers=h); assert r.status_code==200,r.text; return r.json()
    ed=post('/auth/register',dict(name='Editor',email='editor@studio.test',password='secure-password',workspaceName='Studio')); eh={'Authorization':'Bearer '+ed['token']}
    inv=post('/invites',{'email':'client@studio.test'},eh); cl=post('/auth/accept-invite',dict(token=inv['token'],name='Client',password='secure-password')); ch={'Authorization':'Bearer '+cl['token']}
    e=post('/episodes',dict(title='Title',brand='Brand',source='source reference',duration=180,prohibitedClaims='None'),ch); base='/episodes/'+e['id']
    e=c.put('/api'+base+'/brief',headers=ch,json={**e,'rights':True}).json()
    e=post(base+'/actions/submit',{'revision':e['revision']},ch)
    e=post(base+'/actions/ready',dict(revision=e['revision'],sourceUsable=True,scopeConfirmed=True,editorQualified=True,paymentPathConfirmed=True),eh)
    for i in range(1,4):
        e=c.put('/api'+base+f'/clips/clip-{i}',headers=eh,json=dict(revision=e['revision'],title='Title',start=0,end=30,quote='quote',context='context',post='post',checked=True)).json()
    e=c.put('/api'+base+'/chapters',headers=eh,json=dict(revision=e['revision'],chapters='00:00 Intro')).json()
    e=post(base+'/actions/send',dict(revision=e['revision']),eh)
    for i in range(1,4): e=post(base+'/actions/approve',dict(revision=e['revision'],version=1,clipId=f'clip-{i}',read=True),ch)
    media=tmp_path/'real.mp4'; generate(media)
    for kind,cid,filename,content in [('project',None,'project.json',b'{"editable":true}')]+[(kind,f'clip-{i}',filename,content) for i in range(1,4) for kind,filename,content in [('video','real.mp4',media.read_bytes()),('subtitle','captions.srt',b'1\n00:00:00,000 --> 00:00:29,000\nHello world\n')]]:
        fields={'revision':e['revision'],'kind':kind}
        if cid: fields['clipId']=cid
        r=c.post('/api'+base+'/assets',headers=eh,data=fields,files={'file':(filename,content)}); assert r.status_code==200,r.text; e=r.json()
        aid=e['assets'][-1]['id']; assert c.get('/api'+base+f'/assets/{aid}/download',headers=ch).content==content
        e=post(base+f'/assets/{aid}/check',dict(revision=e['revision'],checked=True),eh)
    e=post(base+'/actions/deliver',dict(revision=e['revision']),eh); assert e['phase']=='delivered'
    e=post(base+'/actions/accept',dict(revision=e['revision'],read=True),ch); assert e['phase']=='accepted'
    assert c.post('/api'+base+'/actions/reopen',headers=eh,json=dict(revision=e['revision'],reason='new source')).status_code==422
    backup(data,tmp_path/'backup'); restore(tmp_path/'backup',tmp_path/'restore')
    restored=TestClient(create_app(data_dir=tmp_path/'restore')); assert restored.get('/api'+base,headers=ch).json()==e
    assert restored.get('/api'+base+f'/assets/{aid}/download',headers=ch).status_code==200
    with pytest.raises(ValueError): restore(tmp_path/'backup',data)
