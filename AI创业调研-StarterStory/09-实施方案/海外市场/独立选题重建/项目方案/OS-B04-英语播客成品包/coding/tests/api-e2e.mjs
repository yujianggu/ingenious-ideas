import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const base=process.env.API_URL||'http://127.0.0.1:8000/api';
const run=Date.now().toString(36);let count=0;
async function api(path,{token,method='GET',body,status=200}={}){
 const r=await fetch(base+path,{method,headers:{...(token?{Authorization:`Bearer ${token}`} :{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
 const raw=await r.text();let data;try{data=JSON.parse(raw)}catch{data=raw}
 assert.equal(r.status,status,`${method} ${path}: ${JSON.stringify(data)}`);count++;return data;
}
async function createStudio(s){return api('/auth/register',{method:'POST',body:{name:`Editor ${s}`,email:`editor-${s}-${run}@example.com`,password:'Test-only-password-442!',workspaceName:`E2E Studio ${s}`},status:200});}
async function invite(owner,s){const i=await api('/invites',{token:owner.token,method:'POST',body:{email:`client-${s}-${run}@example.com`},status:200});return api('/auth/accept-invite',{method:'POST',body:{token:i.token,name:`Client ${s}`,password:'Test-only-password-552!'},status:200});}
const editor=await createStudio('one'),outsider=await createStudio('two');
const client=await invite(editor,'one'),otherClient=await invite(editor,'two');
const headers=t=>({Authorization:`Bearer ${t}`});
const brief={title:'Customer research interview',brand:'Integration Studio',source:'Authorized editorial source supplied directly',duration:180,notes:'Retain limitations',glossary:'API; pilot',prohibitedClaims:'No universal performance claims'};
let ep=await api('/episodes',{token:client.token,method:'POST',body:brief,status:200});const id=ep.id;
await api(`/episodes/${id}`,{token:otherClient.token,status:404});
await api(`/episodes/${id}`,{token:outsider.token,status:404});
async function action(name,body={},actor=editor,status=200){const value=await api(`/episodes/${id}/actions/${name}`,{token:actor.token,method:'POST',body:{revision:ep.revision,...body},status});if(status===200)ep=value;return value;}
await action('submit',{},client,422);
ep=await api(`/episodes/${id}/brief`,{token:client.token,method:'PUT',body:{revision:ep.revision,...brief,rights:true}});
await action('submit',{},client);assert.equal(ep.phase,'submitted');
await action('ready',{sourceUsable:true,scopeConfirmed:true,editorQualified:true,paymentPathConfirmed:true});assert.equal(ep.phase,'editing');assert.ok(Math.abs(Date.parse(ep.dueAt)-Date.parse(ep.readyAt)-7*86400000)<1000);
async function clips(){for(let i=0;i<3;i++)ep=await api(`/episodes/${id}/clips/clip-${i+1}`,{token:editor.token,method:'PUT',body:{revision:ep.revision,title:`Excerpt ${i+1}`,start:i*40,end:i*40+30,quote:'The small pilot was helpful. We have not tested this across other teams.',context:'Small pilot, not a universal conclusion.',post:'Learn from one small pilot.',checked:true}});}
await clips();ep=await api(`/episodes/${id}/chapters`,{token:editor.token,method:'PUT',body:{revision:ep.revision,chapters:'00:00 Introduction\n00:40 Pilot\n01:20 Limits'}});
await action('send');assert.equal(ep.phase,'review');
const oldRevision=ep.revision;
await action('approve',{version:ep.version,clipId:'clip-1',read:true},client);
await api(`/episodes/${id}/actions/approve`,{token:client.token,method:'POST',body:{revision:oldRevision,version:ep.version,clipId:'clip-2',read:true},status:409});
await action('changes',{version:ep.version,clipId:'clip-2',feedback:'Keep the limited sample size visible.'},client);
await action('deliver',{},editor,422);
await action('revise',{reason:'Preserve sample qualifier'});assert.equal(ep.version,2);assert.ok(ep.clips.every(c=>c.status==='draft'&&!c.checked));assert.equal(ep.history.length,1);
await clips();await action('send');for(let i=1;i<=3;i++)await action('approve',{version:ep.version,clipId:`clip-${i}`,read:true},client);
assert.equal(ep.phase,'approved');await action('deliver',{},editor,422);
async function upload(kind,clipId,filename,data,type,status=200){const body=new FormData();body.append('revision',String(ep.revision));body.append('kind',kind);if(clipId)body.append('clipId',clipId);body.append('file',new Blob([data],{type}),filename);const r=await fetch(`${base}/episodes/${id}/assets`,{method:'POST',headers:headers(editor.token),body});const result=await r.json();assert.equal(r.status,status,JSON.stringify(result));count++;if(status===200)ep=result;}
await upload('video','clip-1','fake.mp4','not video','video/mp4',422);
const video=await readFile(process.env.TEST_VIDEO||'/tmp/episode-desk-test.mp4');
for(let i=1;i<=3;i++){
 await upload('video',`clip-${i}`,`clip-${i}.mp4`,video,'video/mp4');
 await upload('subtitle',`clip-${i}`,`clip-${i}.srt`,'1\n00:00:00,000 --> 00:00:10,000\nThe small pilot was helpful.\n\n2\n00:00:10,000 --> 00:00:29,900\nWe have not tested across other teams.\n','text/plain');
}
await upload('project',null,'editable-review.txt','Editable source intervals and verified transcript: '+JSON.stringify(ep.clips),'text/plain');
await action('deliver',{},editor,422);
for(const a of [...ep.assets].filter(a=>a.version===ep.version&&a.kind!=='source'))ep=await api(`/episodes/${id}/assets/${a.id}/check`,{token:editor.token,method:'POST',body:{revision:ep.revision,checked:true}});
const asset=ep.assets.find(a=>a.kind==='video'&&a.version===ep.version);
await api(`/episodes/${id}/assets/${asset.id}/download`,{token:otherClient.token,status:404});
const download=await fetch(`${base}/episodes/${id}/assets/${asset.id}/download`,{headers:headers(client.token)});assert.equal(download.status,200);assert.equal((await download.arrayBuffer()).byteLength,video.length);count++;
await action('deliver');assert.equal(ep.phase,'delivered');
await action('accept',{read:true},client);assert.equal(ep.phase,'accepted');
await action('reopen',{reason:'Cannot reopen accepted'},client,422);
const manifest=await api(`/episodes/${id}/export`,{token:client.token});assert.ok(manifest);
await api('/auth/logout',{token:client.token,method:'POST'});await api('/auth/me',{token:client.token,status:401});
console.log(`PASS ${count} HTTP integration assertions: auth/isolation, brief, timing, review, conflict, revision, real assets/QC, download, delivery, receipt, logout.`);
