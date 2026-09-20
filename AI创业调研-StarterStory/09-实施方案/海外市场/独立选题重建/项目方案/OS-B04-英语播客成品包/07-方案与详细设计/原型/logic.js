/* Pure review-workflow model. No media rendering or production authorization. */
(function(root){
'use strict';
const copy=x=>JSON.parse(JSON.stringify(x));
const check=(ok,message)=>{if(!ok)throw new Error(message);};
const text=x=>typeof x==='string';
const finite=(x,min,max)=>Number.isFinite(x)&&x>=min&&x<=max;
const uid=()=>globalThis.crypto?.randomUUID?.()||'episode-'+Date.now()+'-'+Math.random().toString(36).slice(2);
function fresh(){return {id:uid(),title:'Turning customer interviews into better decisions',brand:'Northline Studio',source:'DEMO-EP12.mp4 — fictional sample',duration:1800,notes:'Preserve qualifications. Do not imply that a small trial proves a general result.',glossary:'Northline; customer discovery; pilot',rights:false,phase:'draft',version:1,clips:[
 {id:'clip-1',title:'A useful question to ask',start:120,end:165,quote:'We began by asking what happened the last time the customer faced the problem.',context:'The guest is describing a research method, not a claim about every customer.',post:'Start with a recent example, not a hypothetical promise.',checked:false,status:'draft',feedback:''},
 {id:'clip-2',title:'What the pilot actually showed',start:480,end:540,quote:'In our small pilot, the team found the review faster. We have not tested this across other teams.',context:'Keep the small-pilot qualifier and the limit on generalizing this result.',post:'A faster review in one pilot is a useful signal—not a universal result.',checked:false,status:'draft',feedback:''},
 {id:'clip-3',title:'Keep the decision with the team',start:960,end:1005,quote:'The draft gives us a starting point. A person still checks the source and decides what to publish.',context:'Human review remains part of the process.',post:'A draft can help. Source checks and publishing decisions still need an owner.',checked:false,status:'draft',feedback:''}
],chapters:'00:00 — Opening and episode question\n02:00 — Recent customer tasks\n08:00 — Pilot results and limits\n16:00 — Review ownership',history:[],events:[],manifest:null};}
function clipCheck(c,e){check(text(c.title)&&c.title.trim()&&text(c.quote)&&c.quote.trim()&&text(c.context)&&c.context.trim()&&text(c.post)&&c.post.trim(),'Add the title, exact excerpt, context note and post copy.');check(Number.isInteger(c.start)&&Number.isInteger(c.end)&&c.start>=0&&c.end<=e.duration&&c.end-c.start>=30&&c.end-c.start<=90,'Each clip must be 30–90 seconds, inside the source duration.');}
function note(e,action){e.events.push({time:new Date().toISOString(),version:e.version,action});}
function requireRole(role,expected){check(role===expected,'This action belongs to the '+expected+' workspace.');}
function current(e){return e.history.find(v=>v.version===e.version);}
function recordDecision(e){const h=current(e);if(h)h.decisions=e.clips.map(c=>({id:c.id,status:c.status,feedback:c.feedback}));}
function transition(original,action,p={},role='client'){
 const e=copy(original),c=e.clips.find(c=>c.id===p.clipId);
 switch(action){
 case 'brief':
  requireRole(role,'client');check(e.phase==='draft','Submitted briefs are locked in this prototype. Create a new episode for a different source.');
  for(const k of ['title','brand','source','notes','glossary'])if(k in p)e[k]=String(p[k]);
  e.duration=Number(p.duration);e.rights=!!p.rights;
  check(e.title.trim()&&e.brand.trim()&&e.source.trim(),'Add an episode title, studio and source reference.');
  check(Number.isInteger(e.duration)&&finite(e.duration,90,3600),'The package supports 90–3600 seconds of source media.');
  check(e.rights,'Confirm that you may share and repurpose this source.');e.phase='editing';note(e,'Brief submitted; source authorization declared in prototype');break;
 case 'saveClip':
  requireRole(role,'editor');check(e.phase==='editing'&&c,'Open an editing revision first.');
  for(const k of ['title','quote','context','post'])c[k]=String(p[k]??c[k]);c.start=Number(p.start);c.end=Number(p.end);c.checked=!!p.checked;
  clipCheck(c,e);c.status='draft';c.feedback='';note(e,'Saved '+c.id+' at '+c.start+'–'+c.end+' seconds');break;
 case 'saveChapters':requireRole(role,'editor');check(e.phase==='editing','Chapter notes are frozen during review.');check(text(p.chapters)&&p.chapters.trim(),'Add chapter notes.');e.chapters=p.chapters;note(e,'Chapter notes updated');break;
 case 'send':
  requireRole(role,'editor');check(e.phase==='editing','Only an editing draft can be sent.');e.clips.forEach(c=>{clipCheck(c,e);check(c.checked,'Check each excerpt against its source and context before review.');});
  check(e.chapters.trim(),'Add chapter notes.');e.clips.forEach(c=>c.status='pending');e.phase='review';
  e.history.push({version:e.version,sentAt:new Date().toISOString(),title:e.title,source:e.source,duration:e.duration,chapters:e.chapters,clips:copy(e.clips),decisions:e.clips.map(c=>({id:c.id,status:c.status,feedback:''}))});note(e,'Version '+e.version+' sent for client review');break;
 case 'approve':
  requireRole(role,'client');check(e.phase==='review'&&c&&c.status==='pending','This clip is not waiting for approval.');check(p.version===e.version,'This review belongs to an old version. Reload the current review.');check(p.read,'Read the excerpt and its context before approving.');c.status='approved';recordDecision(e);note(e,'Client approved '+c.id);if(e.clips.every(c=>c.status==='approved'))e.phase='approved';break;
 case 'changes':
  requireRole(role,'client');check(e.phase==='review'&&c&&c.status==='pending','This clip is not waiting for feedback.');check(p.version===e.version,'This review belongs to an old version.');check(text(p.feedback)&&p.feedback.trim(),'Explain what needs to change.');c.status='changes';c.feedback=p.feedback.trim();recordDecision(e);note(e,'Client requested changes to '+c.id+': '+c.feedback);break;
 case 'revision':
  requireRole(role,'editor');check(['review','approved','manifest'].includes(e.phase),'There is no sent version to revise.');
  recordDecision(e);e.version++;e.phase='editing';e.manifest=null;e.clips.forEach(c=>{c.status='draft';c.checked=false;});note(e,'New revision started; all approvals must be renewed');break;
 case 'manifest':
  requireRole(role,'editor');check(e.phase==='approved','All three current clips must be approved first.');
  e.manifest={type:'prototype-review-manifest',episodeId:e.id,version:e.version,title:e.title,source:e.source,generatedAt:new Date().toISOString(),status:'review-approved-media-not-rendered',missingProductionAssets:['3 rendered MP4 clips','source-checked timed captions','editable production project','actual media quality acceptance'],clips:copy(e.clips),chapters:e.chapters};e.phase='manifest';note(e,'Review manifest prepared; production media still required');break;
 default:throw Error('Unknown action.');
 }
 return e;
}
function validate(db){
 check(db&&db.schema===1&&db.product==='OS-B04'&&Array.isArray(db.episodes)&&db.episodes.length>0&&db.episodes.length<=20,'Unsupported backup or episode limit exceeded.');
 const ids=new Set(),allowed=['pending','approved','changes'];
 const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 const content=c=>Object.fromEntries(['id','title','start','end','quote','context','post','checked'].map(k=>[k,c[k]]));
 const validDate=x=>text(x)&&Number.isFinite(Date.parse(x));
 const clipShape=c=>c&&['id','title','quote','context','post','feedback'].every(k=>text(c[k]))&&!!c.id.trim()&&typeof c.checked==='boolean'&&['draft',...allowed].includes(c.status)&&Number.isInteger(c.start)&&Number.isInteger(c.end);
 for(const e of db.episodes){
  check(e&&text(e.id)&&e.id.trim()&&!ids.has(e.id)&&['draft','editing','review','approved','manifest'].includes(e.phase)&&Number.isInteger(e.version)&&e.version>0,'Invalid episode identity or state.');ids.add(e.id);
  check(['title','brand','source','notes','glossary','chapters'].every(k=>text(e[k]))&&typeof e.rights==='boolean'&&Number.isInteger(e.duration)&&finite(e.duration,90,3600),'Invalid episode fields.');
  check(Array.isArray(e.clips)&&e.clips.length===3&&e.clips.every(clipShape)&&new Set(e.clips.map(c=>c.id)).size===3,'A review must contain three valid, unique clips.');
  if(e.phase!=='draft')check(e.rights&&e.title.trim()&&e.brand.trim()&&e.source.trim(),'Submitted work requires an authorized source and complete brief.');
  if(['draft','editing'].includes(e.phase))check(e.clips.every(c=>c.status==='draft'),'An editing draft cannot carry an approval.');
  if(['review','approved','manifest'].includes(e.phase))e.clips.forEach(c=>{clipCheck(c,e);check(c.checked&&allowed.includes(c.status),'Sent clips require source checks and valid review states.');});
  check(Array.isArray(e.events)&&e.events.every(v=>v&&validDate(v.time)&&text(v.action)&&Number.isInteger(v.version)&&v.version>0&&v.version<=e.version)&&Array.isArray(e.history),'Invalid event or history.');
  check(new Set(e.history.map(h=>h.version)).size===e.history.length,'Repeated review version.');
  for(const h of e.history){
   check(h&&Number.isInteger(h.version)&&h.version>0&&h.version<=e.version&&validDate(h.sentAt)&&text(h.title)&&text(h.source)&&text(h.chapters)&&Number.isInteger(h.duration)&&finite(h.duration,90,3600)&&Array.isArray(h.clips)&&h.clips.length===3&&Array.isArray(h.decisions)&&h.decisions.length===3,'Invalid review snapshot.');
   check(h.clips.every(clipShape)&&new Set(h.clips.map(c=>c.id)).size===3&&same(h.clips.map(c=>c.id),e.clips.map(c=>c.id)),'Snapshot clip identities do not match.');
   h.clips.forEach(c=>{clipCheck(c,h);check(c.checked&&c.status==='pending','Snapshot must be the checked review submission.');});
   check(h.decisions.every((d,i)=>d&&d.id===h.clips[i].id&&allowed.includes(d.status)&&text(d.feedback)&&(d.status!=='changes'||d.feedback.trim())),'Invalid review decision.');
  }
  if(['review','approved','manifest'].includes(e.phase)){
   const h=current(e);check(h&&h.title===e.title&&h.source===e.source&&h.duration===e.duration&&h.chapters===e.chapters&&same(h.clips.map(content),e.clips.map(content)),'Frozen review and current content differ.');
   check(same(h.decisions,e.clips.map(c=>({id:c.id,status:c.status,feedback:c.feedback}))),'Current approval and review history differ.');
  }
  if(['approved','manifest'].includes(e.phase))check(e.clips.every(c=>c.status==='approved'),'Approval state is inconsistent.');
  if(e.phase==='review')check(!e.clips.every(c=>c.status==='approved'),'Fully approved review has the wrong phase.');
  if(e.manifest!==null){
   const m=e.manifest;
   check(e.phase==='manifest'&&m?.type==='prototype-review-manifest'&&m.episodeId===e.id&&m.version===e.version&&m.title===e.title&&m.source===e.source&&m.chapters===e.chapters&&validDate(m.generatedAt)&&m.status==='review-approved-media-not-rendered'&&same(m.clips,e.clips),'Invalid manifest or unapproved manifest content.');
   check(same(m.missingProductionAssets,['3 rendered MP4 clips','source-checked timed captions','editable production project','actual media quality acceptance']),'Manifest must identify outstanding production assets.');
  }
  if(e.phase==='manifest')check(e.manifest!==null,'Missing manifest.');
 }
 return db;
}
function timestamp(seconds){const ms=Math.round(seconds*1000);return [Math.floor(ms/3600000),Math.floor(ms/60000)%60,Math.floor(ms/1000)%60].map(x=>String(x).padStart(2,'0')).join(':')+','+String(ms%1000).padStart(3,'0');}
function srt(c){return '1\n'+timestamp(0)+' --> '+timestamp(c.end-c.start)+'\n'+c.quote+'\n';}
const api={copy,fresh,transition,validate,srt,clipCheck};if(typeof module!=='undefined')module.exports=api;else root.Studio=api;
})(globalThis);
