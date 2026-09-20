'use strict';
const cfg=JSON.parse(document.getElementById('config').textContent),code=cfg.code;
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone=v=>JSON.parse(JSON.stringify(v));
const key='overseas-design-v1-'+code;
const fresh=()=>({id:crypto.randomUUID(),form:Object.fromEntries(cfg.fields.map(f=>[f.key,f.value])),status:'draft',version:1,history:[],deliveries:[],events:[],items:[{name:'Bank statement',status:'Missing'},{name:'Business receipts',status:'Missing'}],result:null,stock:null,need:null,last:null,known:true,reason:'',reviewed:false});
let db={schema:1,code,records:[fresh()]},active=0,role=0,view='work',notice='',error=false,pending=null,scanFiles=[[],[]],busy=false;
try{const saved=JSON.parse(localStorage.getItem(key));if(saved){validate(saved);db=saved;}}catch{notice='Saved data could not be read. The example is open; the stored copy has not been overwritten.';error=true;}
const r=()=>db.records[active];
function validate(v){
 if(v?.schema!==1||v.code!==code||!Array.isArray(v.records)||!v.records.length||v.records.length>100)throw Error('Unsupported or damaged backup. Your current records are unchanged.');
 const ids=new Set(),colours=new Set();
 const text=v=>typeof v==='string';
 const amount=v=>Number.isSafeInteger(v)&&Math.abs(v)<=1e12;
 const half=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1000000&&v*2%1===0;
 const validResult=z=>{if(z===null)return true;if(!z||typeof z!=='object')return false;
 if(code==='C13')return text(z.method)&&text(z.time)&&Array.isArray(z.rows)&&z.rows.every(q=>q&&text(q.left)&&text(q.right)&&text(q.status));
 if(code==='B10')return text(z.period)&&text(z.source)&&['sales','refund','ads','net'].every(k=>amount(z[k]));
 if(code==='B05')return ['question','answer','source'].every(k=>text(z[k]));
 if(code==='C01')return text(z.title)&&text(z.draft);
 if(code==='B17')return ['title','location','attachment'].every(k=>text(z[k]))&&typeof z.qty==='number'&&Number.isFinite(z.qty)&&z.qty>0&&z.qty<=1000000&&amount(z.price)&&amount(z.total);
 return false;};
 const statuses=['draft','sent','confirmed','rejected','checked','submitted','delivered','accepted','revision','reserved','out','inspection','damaged','ready','open','saved','scanned','cancelled'];
 for(const x of v.records){if(!x||typeof x.id!=='string'||ids.has(x.id)||!x.form||!cfg.fields.every(f=>typeof x.form[f.key]==='string')||!statuses.includes(x.status)||!Array.isArray(x.events)||!x.events.every(e=>typeof e.text==='string'&&typeof e.time==='string')||!Array.isArray(x.deliveries)||!Array.isArray(x.history)||!Number.isInteger(x.version)||x.version<1||!Array.isArray(x.items)||x.items.length!==2||!x.items.every(i=>typeof i.name==='string'&&['Missing','Pending review','Approved','Returned','Not applicable'].includes(i.status))||typeof x.known!=='boolean'||typeof x.reason!=='string'||typeof x.reviewed!=='boolean')throw Error('Invalid record in backup. Nothing was replaced.');
 if(code==='C16'&&![x.stock,x.need].every(n=>n===null||typeof n==='number'&&Number.isFinite(n)&&n>=0&&n*2%1===0))throw Error('Invalid thread quantities. Nothing was replaced.');
 if(!validResult(x.result)||!x.deliveries.every(d=>d&&text(d.time)&&d.result!==null&&validResult(d.result))||!x.history.every(h=>h&&Number.isInteger(h.version)&&h.version>0&&text(h.content)&&text(h.status))||!x.items.every(i=>(i.file===undefined||text(i.file))&&(i.reason===undefined||text(i.reason))))throw Error('Invalid result or history. Nothing was replaced.');
 if(code==='C16'){const colour=x.form.title.trim();if((x.status!=='draft'&&(!colour||colours.has(colour)))||(x.last!==null&&(!x.last||!half(x.last.stock)))||![x.stock,x.need].every(n=>n===null||half(n)))throw Error('Duplicate colour or invalid stock history. Nothing was replaced.');if(x.status!=='draft')colours.add(colour);}
 if(code==='C18'&&x.last!==null&&(!x.last||!text(x.last.text)))throw Error('Invalid care history. Nothing was replaced.');
 if(code==='C16'&&x.status==='saved'&&(x.need===null||(x.known&&x.stock===null)||(!x.known&&x.stock!==null)))throw Error('Inconsistent saved stock. Nothing was replaced.');
 if(code==='B16'&&['sent','confirmed','checked','rejected'].includes(x.status)&&!x.history.some(h=>h.version===x.version))throw Error('Missing current proof version. Nothing was replaced.');
 ids.add(x.id);}
}
function persist(){try{localStorage.setItem(key,JSON.stringify(db));$('saved').textContent='Saved on this browser';}catch{$('saved').textContent='Not saved — export a backup before closing';}}
function log(text){r().events.unshift({text,time:new Date().toISOString()});}
function guard(ok,msg){if(!ok)throw Error(msg);}
function req(...keys){guard(keys.every(k=>String(r().form[k]??'').trim()),'Complete the required fields before continuing.');}
function num(k){const x=r().form[k];guard(x.trim()!==''&&Number.isFinite(Number(x))&&Number(x)>=0,'Enter a valid non-negative number.');guard(Number(x)<=1000000,'Value exceeds the 1,000,000 prototype limit.');return Number(x);}
function cents(k){const x=r().form[k];guard(/^\d+(\.\d{1,2})?$/.test(x),'Use a non-negative amount with at most two decimal places.');const n=Math.round(Number(x)*100);guard(Number.isSafeInteger(n)&&n<=1e12,'Amount exceeds the supported sample range.');return n;}
function validDeliveryAmount(z){return !['B10','B17'].includes(code)||['net','total'].filter(k=>k in z).every(k=>Number.isSafeInteger(z[k])&&Math.abs(z[k])<=1e12);}
const usd=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n/100);
const local=['C13','C16','C18'].includes(code);
const external=()=>['B16','B18'].includes(code)&&role===1;
const service=['B10','B05','C01','B17'].includes(code);
const btn=(label,act,off=false,secondary=false)=>`<button type="button" data-act="${act}" ${off?'disabled':''} class="${secondary?'secondary':''}">${esc(label)}</button>`;
const pill=t=>`<span class="pill">${esc(t)}</span>`;
const card=(title,body)=>`<section class="card"><h2>${esc(title)}</h2>${body}</section>`;
const row=(a,b)=>`<div class="row"><span>${esc(a)}</span><strong>${esc(b)}</strong></div>`;
function field(k,disabled=false){const f=cfg.fields.find(f=>f.key===k);return `<label>${esc(f.label)}${f.type==='textarea'?`<textarea data-field="${k}" ${disabled?'disabled':''}>${esc(r().form[k])}</textarea>`:`<input data-field="${k}" type="${f.type}" value="${esc(r().form[k])}" ${disabled?'disabled':''} ${f.type==='number'?'min="0" step="0.5"':''}>`}</label>`;}
const fields=(ks,locked=false)=>ks.map(k=>field(k,locked)).join('');
const feedback=()=>`<label>Revision notes<textarea id="reason">${esc(r().reason)}</textarea></label>`;
const check=(label)=>`<label class="check"><input id="reviewed" type="checkbox" ${r().reviewed?'checked':''}>${esc(label)}</label>`;
const empty=t=>`<div class="empty">${esc(t)}</div>`;
function timeline(){return r().events.length?`<ol class="timeline">${r().events.map(e=>`<li>${esc(e.text)}<time>${esc(e.time)}</time></li>`).join('')}</ol>`:empty('Activity will appear here after your first action.');}
function result(){const x=r().result;if(!x)return empty('Your result will appear here when it is ready.');if(code==='B10')return `<p>${esc(x.period)}</p>${row('Receipts less refunds',usd(x.net))}${row('Advertising spend',usd(x.ads))}<p class="note">${usd(x.sales)} − ${usd(x.refund)} = ${usd(x.net)}. This is not net profit.</p><p>Source batch: ${esc(x.source)} · Rule: period-cash-v1</p>`;if(code==='B05')return `<h3>${esc(x.question)}</h3><p>Answer: ${esc(x.answer)}</p><p>Source: ${esc(x.source)}</p><p class="note">Calibration sample only. The full 100-question pack requires a separate review.</p>`;if(code==='C01')return `<h3>${esc(x.title)}</h3><div class="document">${esc(x.draft)}</div>`;if(code==='B17')return `<h3>${esc(x.title)}</h3>${row('Location',x.location)}${row('Quantity × unit price',x.qty+' × '+usd(x.price))}${row('Proposed amount',usd(x.total))}<p>Original attachment reference: ${esc(x.attachment)}</p><p class="note">Receipt is not approval or an agreement to pay.</p>`;return `<pre>${esc(JSON.stringify(x,null,2))}</pre>`;}
function work(){const x=r(),f=x.form;
 if(code==='B16'){
  if(role===1)return card('Review this proof',x.status==='draft'?empty('The studio has not sent this version yet.'):`${pill('Version '+x.version)}<h3>${esc(f.title)}</h3><div class="document">${esc(f.content)}</div>${check('I have read this version carefully')}${feedback()}<div class="actions">${btn('Confirm this version','confirm',x.status!=='sent')}${btn('Request changes','reject',x.status!=='sent',true)}</div>`);
  return card('Order & current proof',fields(['title','customer','content'],x.status!=='draft')+`<div class="actions">${btn('Send for confirmation','send',x.status!=='draft')}${btn('Create new version','newVersion',x.status==='draft',true)}</div><p class="note">Editable text stands in for the artwork preview. No email is sent.</p>`)+card('Print handoff',`${pill(x.status)}<p>Only the current confirmed version can be checked for print.</p>${btn('Record print check','printCheck',x.status!=='confirmed')}`);
 }
 if(service){const ks=code==='B05'?['title','material']:code==='C01'?['title','experience']:cfg.fields.map(f=>f.key);const producer=role===1;
  let input=producer?`<h3>${esc(f.title)}</h3><div class="document">${esc(code==='B05'?f.material:code==='C01'?f.experience:'Submitted source batch is frozen for this version.')}</div>`:fields(ks,x.status!=='draft');
  if(producer){if(code==='B05')input+=fields(['question','answer','source'],!['submitted','revision'].includes(x.status));if(code==='C01')input+=field('draft',!['submitted','revision'].includes(x.status));if(['B10','B17'].includes(code))input+=fields(ks,true);input+=`<p class="note">${esc(x.reason)}</p>${check(code==='B05'?'I checked the answer against the cited source':code==='C01'?'I checked the draft against the supplied facts':'I checked the inputs and the delivery rules')}${btn('Deliver reviewed version','produce',!['submitted','revision'].includes(x.status))}`;}
  else input+=`<div class="actions">${btn('Submit brief','submit',x.status!=='draft')}${btn('Edit source & resubmit','amend',x.status==='draft',true)}</div>`;
  let out=result();if(x.result&&!producer)out+=`${feedback()}${code==='C01'?check('I checked the facts in this version'):''}<div class="actions">${btn(code==='B17'?'Record receipt':code==='B05'?'Accept sample scope':'Accept this version','accept',x.status!=='delivered')}${btn('Request revision','revise',x.status!=='delivered',true)}</div>`;
  return card(producer?'Review workbench':'Your brief',input)+card(code==='B05'?'Reviewed sample':'Your delivery',out);
 }
 if(code==='B12')return card('Order & equipment',fields(['title','equipment','start','end'],x.status!=='draft')+`<p class="note">All sample times are UTC. One serialized item per order.</p>${btn('Reserve equipment','reserve',x.status!=='draft')}${feedback()}${btn('Cancel reservation','cancelReservation',x.status!=='reserved',true)}`)+card('Warehouse handoff',`${pill(x.status)}<div class="actions">${btn('Check out','out',x.status!=='reserved')}${btn('Record return','return',x.status!=='out')}${btn('Inspection passed','ready',x.status!=='inspection')}${btn('Mark damaged','damage',x.status!=='inspection',true)}${btn('Repair complete','repair',x.status!=='damaged',true)}</div><p class="note">Returned equipment is unavailable until inspection passes.</p>`);
 if(code==='B18'){
  const header=role===0?fields(['title','period'],x.status!=='draft')+btn('Open monthly checklist','open',x.status!=='draft'):`<h3>${esc(f.title)} · ${esc(f.period)}</h3>`;
  return card('Monthly checklist',header+x.items.map((i,n)=>`<article class="item"><h3>${esc(i.name)}</h3>${pill(i.status)}${i.file?`<p>${esc(i.file)}</p>`:''}${i.reason?`<p class="feedback">${esc(i.reason)}</p>`:''}${role===1?`<label>Choose ${esc(i.name)}<input type="file" data-upload="${n}" ${x.status==='draft'?'disabled':''}></label><p class="note">Only the filename is retained in this browser prototype. File contents are not uploaded or saved.</p>`:`<label>Review note for ${esc(i.name)}<input data-itemnote="${n}" value="${esc(i.reason||'')}"></label><div class="actions">${btn('Approve '+i.name,'approve:'+n,i.status!=='Pending review')}${btn('Return '+i.name,'returnItem:'+n,i.status!=='Pending review',true)}${btn('Not applicable: '+i.name,'na:'+n,x.status==='draft',true)}</div>`}</article>`).join(''))+card('Completeness',x.items.every(i=>['Approved','Not applicable'].includes(i.status))?pill('Complete after review'):empty('Still missing or awaiting review. Uploading alone does not complete this checklist.'));
 }
 if(code==='C13')return card('Choose files to compare',field('title')+`<p>Select small files from two folders. Nothing is uploaded or deleted. Limit: 20 files / 20 MB per side.</p><p class="note">Selected left: ${esc(scanFiles[0].map(f=>f.name).join(', ')||'none')}<br>Selected right: ${esc(scanFiles[1].map(f=>f.name).join(', ')||'none')}</p><label>Left folder files<input id="leftFiles" type="file" multiple></label><label>Right folder files<input id="rightFiles" type="file" multiple></label><div class="actions">${btn(busy?'Comparing…':'Compare selected files','scan',busy)}${btn('Use sample comparison','sample',busy,true)}</div><p class="note">Selected files stay in memory until you switch records or reload. A saved report describes a past check.</p>`)+card('Comparison results',x.result?`<p>${esc(x.result.method)} · ${esc(x.result.time)}</p>${x.result.rows.map(t=>`<div class="item"><strong>${esc(t.left)} ↔ ${esc(t.right)}</strong><p>${esc(t.status)}</p></div>`).join('')}`:empty('Select both sides to begin.'));
 if(code==='C16')return card('Thread & project',field('title',x.status!=='draft')+`<label class="check"><input id="known" type="checkbox" ${x.known?'checked':''}>Stock quantity is known</label>`+field('stock',!x.known)+field('need')+btn('Update shopping list','calculate'))+card('Shopping list',x.status==='saved'?`${row('Colour code',f.title)}${row('Available',x.stock===null?'Needs checking':x.stock+' skeins')}${row('To buy',x.stock===null?'Unknown':Math.max(0,x.need-x.stock)+' skeins')}<div class="actions">${btn('Record purchase','purchase',x.stock===null||x.stock>=x.need)}${btn('Use half a skein','consume',x.stock===null||x.stock<0.5)}${btn('Undo last stock change','undoStock',!x.last,true)}</div>`:empty('Save a known quantity or mark it as needing a check.'));
 if(code==='C18')return card('Upcoming checks',`<p class="note">System notifications: not connected. Check this list when you open the journal.</p>${db.records.filter(y=>y.status==='saved').sort((a,b)=>a.form.date.localeCompare(b.form.date)).map(y=>row(y.form.title,y.form.date+(y.form.date<=new Date().toLocaleDateString('en-CA')?' · Due':''))).join('')||empty('Save a plant and its next check date.')}`)+card('Plant & next check',fields(['title','location','date'])+btn('Save plant & check date','savePlant')+`<p class="note">The check date is your choice. This prototype does not send system notifications.</p>`)+card('Care journal',field('note')+`<div class="actions">${btn('Record inspection','inspect',x.status==='draft')}${btn('Record watering','water',x.status==='draft')}${btn('Record feeding','feed',x.status==='draft',true)}${btn('Record repotting','repot',x.status==='draft',true)}${btn('Undo last care event','undoCare',!x.last,true)}</div><p class="note">An inspection does not record watering.</p>`);
}
function downloads(){return card('Deliveries & versions',`${r().deliveries.length?r().deliveries.map((d,i)=>`<details><summary>Delivery ${i+1} · ${esc(d.time)}</summary><pre>${esc(JSON.stringify(d.result,null,2))}</pre></details>`).join(''):empty('No delivered version yet.')}<div class="actions">${btn(code==='C01'?'Download resume text':'Download result record','download',code==='C01'?!r().result:!r().events.length)}</div><p class="note">Downloads are prototype samples. Production file formats and checks are specified in the requirements.</p>${r().history.length?'<h3>Proof versions</h3>'+r().history.map(h=>`<details><summary>Version ${h.version} · ${esc(h.status)}</summary><div class="document">${esc(h.content)}</div></details>`).join(''):''}`);}
function render(){
 $('product').textContent=cfg.name;$('tagline').textContent=cfg.tag;$('roleLabel').textContent=cfg.roles[role];
 $('list').innerHTML=external()?`<p class="note">${esc(r().form.title)}<br>Shared task only</p>`:`<div class="actions">${btn('New '+cfg.noun,'new')}</div>${db.records.map((x,i)=>`<button class="record ${i===active?'selected':''}" data-record="${i}"><strong>${esc(x.form.title||'Untitled')}</strong><span>${esc(x.status)} · v${x.version}</span></button>`).join('')}`;
 $('tabs').innerHTML=['work','deliveries','history'].map(v=>`<button data-view="${v}" class="${view===v?'active':''}">${v[0].toUpperCase()+v.slice(1)}</button>`).join('');
 $('main').innerHTML=view==='work'?work():view==='deliveries'?downloads():card('Activity history',timeline());
 $('notice').textContent=notice;$('notice').hidden=!notice;$('notice').className='notice'+(error?' error':'');
 $('roles').innerHTML=cfg.roles.map((name,i)=>btn('View as '+name,'role:'+i,i===role,true)).join('');
 $('backupTools').hidden=external();$('backupPreview').innerHTML=pending?`<p>${pending.records.length} records will replace this browser's list.</p>${btn('Confirm restore','restore')}${btn('Cancel restore','cancelRestore',false,true)}`:'';
 document.querySelectorAll('[data-act]').forEach(e=>e.onclick=()=>act(e.dataset.act));
 document.querySelectorAll('[data-record]').forEach(e=>e.onclick=()=>{active=Number(e.dataset.record);pending=null;scanFiles=[[],[]];notice='';render();});
 document.querySelectorAll('[data-view]').forEach(e=>e.onclick=()=>{view=e.dataset.view;render();});
 document.querySelectorAll('[data-field]').forEach(e=>e.oninput=()=>{r().form[e.dataset.field]=e.value;if(code==='C16'){r().status='draft';r().last=null;}r().reviewed=false;persist();});
 document.querySelectorAll('[data-itemnote]').forEach(e=>e.oninput=()=>{r().items[Number(e.dataset.itemnote)].reason=e.value;persist();});
 $('reason')?.addEventListener('input',e=>{r().reason=e.target.value;persist();});
 $('reviewed')?.addEventListener('change',e=>{r().reviewed=e.target.checked;persist();});
 $('known')?.addEventListener('change',e=>{r().known=e.target.checked;r().status='draft';r().last=null;persist();render();});
 for(const [id,n] of [['leftFiles',0],['rightFiles',1]])$(id)?.addEventListener('change',e=>{scanFiles[n]=Array.from(e.target.files);render();});
 document.querySelectorAll('[data-upload]').forEach(e=>e.onchange=()=>{if(!e.files[0])return;const item=r().items[Number(e.dataset.upload)];item.file=e.files[0].name;item.status='Pending review';item.reason='';log('File reference submitted: '+item.name);persist();render();});
}
function download(name,value,type='application/json'){const a=document.createElement('a'),u=URL.createObjectURL(new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type}));a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);}
async function compare(){
 guard(scanFiles.every(x=>x.length),'Choose files on both sides.');guard(scanFiles.every(x=>x.length<=20&&x.reduce((n,f)=>n+f.size,0)<=20*1024*1024),'Limit each side to 20 files and 20 MB.');
 const id=r().id,selection=scanFiles.map(a=>a.slice());busy=true;render();
 try{const read=async f=>{try{const bytes=new Uint8Array(await f.arrayBuffer());const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');return {name:f.name,bytes,hash};}catch{return {name:f.name,error:true};}};
 const [a,b]=await Promise.all(selection.map(fs=>Promise.all(fs.map(read))));const rows=[];
 for(const f of a){if(f.error){rows.push({left:f.name,right:'—',status:'Unknown: left file could not be read'});continue;}const matches=b.filter(g=>!g.error&&g.hash===f.hash);if(!matches.length)rows.push({left:f.name,right:'—',status:b.some(g=>g.error)?'No match among readable files; unreadable files remain unknown':'No equal content in selected right files'});for(const g of matches)rows.push({left:f.name,right:g.name,status:f.bytes.length===g.bytes.length&&f.bytes.every((v,i)=>v===g.bytes[i])?'Byte-confirmed identical':'Hash matched; byte comparison differs'});}
 for(const f of b){if(!f.error&&!a.some(g=>!g.error&&g.hash===f.hash))rows.push({left:'—',right:f.name,status:a.some(g=>g.error)?'No match among readable left files; unreadable files remain unknown':'No equal content in selected left files'});}
 for(const f of b)if(f.error)rows.push({left:'—',right:f.name,status:'Unknown: right file could not be read'});
 const target=db.records.find(x=>x.id===id);target.result={method:'User-selected files · SHA-256 + byte comparison · read-only',time:new Date().toISOString(),leftCount:a.length,rightCount:b.length,rows};target.status='scanned';target.events.unshift({text:'Selected files compared; no files modified',time:new Date().toISOString()});notice='Comparison complete. The report covers only the selected files.';
 }finally{busy=false;}
}
async function act(a){if(busy)return;const before=clone(db);error=false;notice='';
 try{const x=r(),f=x.form;
 if(a.startsWith('role:')){role=Number(a.split(':')[1]);view='work';x.reviewed=false;notice='Previewing a different role. Production access must use separate authenticated sessions.';}
 else if(a==='new'){guard(!external(),'Only the workspace owner can create records.');guard(db.records.length<100,'Prototype limit: 100 records. Export before starting a separate workspace.');const created=fresh();created.form.title='';db.records.push(created);active=db.records.length-1;view='work';pending=null;scanFiles=[[],[]];}
 else if(a==='backup'){guard(!external(),'Use the owner workspace to export the complete list.');guard(new Blob([JSON.stringify(db,null,2)]).size<=2000000,'This workspace exceeds the 2 MB restorable backup limit. Export individual result records before reducing the workspace.');download(code+'-backup.json',db);notice='Browser records exported. This does not include uploaded file contents.';}
 else if(a==='restore'){guard(pending,'Choose a valid backup first.');validate(pending);db=clone(pending);active=0;pending=null;scanFiles=[[],[]];notice='Records restored.';}
 else if(a==='cancelRestore'){pending=null;notice='Restore cancelled.';}
 else if(a==='download'){const pack={prototype:true,code,record:x,exportedAt:new Date().toISOString()};download(code+(code==='C01'?'-resume.txt':'-result.json'),code==='C01'?x.result.title+'\n\n'+x.result.draft+'\n\nPrototype sample. No professional review has occurred.':pack,code==='C01'?'text/plain':'application/json');notice='Sample result downloaded.';}
 else if(code==='B16'){
  if(a==='send'){guard(role===0&&x.status==='draft','Create a new version before sending again.');req('title','customer','content');x.status='sent';x.reviewed=false;x.history.push({version:x.version,content:f.content,status:'sent'});log('Version '+x.version+' sent for confirmation');}
  if(a==='newVersion'){guard(role===0&&x.status!=='draft','Only the studio can create the next version.');x.version++;x.status='draft';x.reviewed=false;log('New version created; previous request no longer applies');}
  if(a==='confirm'){guard(role===1&&x.status==='sent'&&x.reviewed,'Read the current version before confirming.');x.status='confirmed';x.history.at(-1).status='confirmed';log('Customer confirmed version '+x.version);}
  if(a==='reject'){guard(role===1&&x.status==='sent'&&x.reason.trim(),'Add revision notes before returning this proof.');x.status='rejected';x.history.at(-1).status='rejected';log('Returned for revision: '+x.reason);}
  if(a==='printCheck'){guard(role===0&&x.status==='confirmed','The current version must be confirmed first.');x.status='checked';log('Print check recorded for version '+x.version);}
 }
 else if(service){
  if(a==='submit'){guard(role===0&&x.status==='draft','Submit from the client or site workspace.');req(...(code==='B05'?['title','material']:code==='C01'?['title','experience']:cfg.fields.map(k=>k.key)));if(code==='B10'){cents('sales');cents('refund');cents('ads');}if(code==='B17'){guard(num('qty')>0,'Quantity must be greater than zero.');cents('price');}x.status='submitted';x.reviewed=false;log('Brief submitted');}
  if(a==='amend'){guard(role===0&&x.status!=='draft','Source is already editable.');x.version++;x.status='draft';x.result=null;x.reviewed=false;log('Source reopened; earlier deliveries remain available');}
  if(a==='produce'){guard(role===1&&['submitted','revision'].includes(x.status)&&x.reviewed,'Check the source and review rules before delivering.');
   if(code==='B10')x.result={period:f.title,sales:cents('sales'),refund:cents('refund'),ads:cents('ads'),net:cents('sales')-cents('refund'),source:f.source};
   if(code==='B05'){req('question','answer','source');x.result={question:f.question,answer:f.answer,source:f.source};}
   if(code==='C01'){req('draft');x.result={title:f.title,draft:f.draft};}
   if(code==='B17')x.result={title:f.title,location:f.location,qty:num('qty'),price:cents('price'),total:Math.round(num('qty')*cents('price')),attachment:f.attachment};
   guard(validDeliveryAmount(x.result),'Calculated amount is outside the supported range.');
   x.deliveries.push({time:new Date().toISOString(),result:clone(x.result)});x.status='delivered';x.reviewed=false;log('Reviewed version '+x.deliveries.length+' delivered');}
  if(a==='accept'){guard(role===0&&x.status==='delivered','Accept a delivered version from the receiving workspace.');if(code==='C01')guard(x.reviewed,'Confirm that you have checked the facts.');x.status='accepted';log(code==='B17'?'Receipt recorded — not approval':code==='B05'?'Sample scope accepted — full pack still requires review':'Current delivery accepted');}
  if(a==='revise'){guard(role===0&&x.status==='delivered'&&x.reason.trim(),'Add revision notes for the delivered version.');x.status='revision';x.reviewed=false;log('Revision requested: '+x.reason);}
 }
 else if(code==='B12'){
  if(a==='cancelReservation'){guard(x.status==='reserved'&&x.reason.trim(),'Only a reservation can be cancelled; add a reason first.');x.status='cancelled';log('Reservation cancelled: '+x.reason);}
  else if(a==='reserve'){req('title','equipment','start','end');f.equipment=f.equipment.trim();guard(x.status==='draft'&&f.start<f.end,'Return must be later than pickup.');guard(!db.records.some(y=>y.id!==x.id&&y.form.equipment.trim()===f.equipment.trim()&&!['draft','cancelled'].includes(y.status)&&y.form.start<f.end&&f.start<y.form.end),'This equipment is already reserved for an overlapping time.');x.status='reserved';log('Equipment reserved in UTC');}
  else {const t={out:['reserved','out'],return:['out','inspection'],ready:['inspection','ready'],damage:['inspection','damaged'],repair:['damaged','inspection']}[a];guard(t&&x.status===t[0],'This transition is not available.');if(a==='out')guard(!db.records.some(y=>y.id!==x.id&&y.form.equipment.trim()===f.equipment.trim()&&['out','inspection','damaged'].includes(y.status)),'This item is still out, awaiting inspection or damaged.');x.status=t[1];log('Equipment: '+t[1]);}
 }
 else if(code==='B18'){
  guard(role===0,'Review actions belong to the bookkeeper.');
  if(a==='open'){req('title','period');guard(x.status==='draft','Checklist already open.');x.status='open';log('Monthly checklist opened');}
  else{const [verb,n]=a.split(':'),i=x.items[Number(n)];guard(i,'Select a valid checklist item.');if(verb==='approve'){guard(i.status==='Pending review','Only pending files can be approved.');i.status='Approved';}else if(verb==='returnItem'){guard(i.status==='Pending review'&&i.reason?.trim(),'Explain why the submitted item needs to be replaced.');i.status='Returned';}else if(verb==='na'){guard(x.status==='open'&&i.reason?.trim(),'Record the reason this item is not applicable.');i.status='Not applicable';}log(i.name+': '+i.status);}
 }
 else if(code==='C13'){
  if(a==='scan')await compare();else if(a==='sample'){x.result={method:'Built-in example — not a disk scan',time:new Date().toISOString(),rows:[{left:'holiday.jpg',right:'holiday-copy.jpg',status:'Example: byte-confirmed identical'},{left:'family.jpg',right:'Unavailable drive',status:'Example: unknown until reconnected'}]};x.status='scanned';log('Sample report opened');}
 }
 else if(code==='C16'){
  if(a==='calculate'){req('title');f.title=f.title.trim();guard(!db.records.some(y=>y.id!==x.id&&y.status!=='draft'&&y.form.title.trim()===f.title.trim()),'This colour already exists. Open its existing record.');x.need=num('need');x.stock=x.known?num('stock'):null;guard([x.need,x.stock].every(n=>n===null||n*2%1===0),'Use whole or half skeins.');x.status='saved';x.last=null;log('Shopping list updated');}
  else{guard(x.status==='saved'&&x.stock!==null,'Save known quantities before changing stock.');if(a==='undoStock'){guard(x.last,'No reversible change.');x.stock=x.last.stock;x.last=null;log('Last stock change reversed');}else{x.last={stock:x.stock};if(a==='purchase'){guard(x.need>x.stock,'Nothing is missing from this colour.');x.stock=x.need;log('Actual purchase recorded');}if(a==='consume'){guard(x.stock>=0.5,'Not enough stock.');x.stock-=0.5;log('Half a skein used');}}f.stock=String(x.stock);}
 }
 else if(code==='C18'){
  if(a==='savePlant'){req('title','date');guard(/^\d{4}-\d{2}-\d{2}$/.test(f.date),'Choose a valid check date.');x.status='saved';log('Plant and next check saved: '+f.date);}
  else{guard(x.status==='saved','Save your plant first.');if(a==='undoCare'){guard(x.last,'No care event to reverse.');log('Correction: reversed '+x.last.text);x.last=null;}else{const names={inspect:'Inspection',water:'Watering',feed:'Feeding',repot:'Repotting'};guard(names[a],'Unknown care action.');x.last={text:names[a]+': '+f.note};log(x.last.text);}}
 }
 if(!notice)notice='Saved.';persist();
 }catch(e){db=before;notice=e.message;error=true;}render();
}
$('backup').onclick=()=>act('backup');
$('restoreFile').onchange=async e=>{pending=null;try{const file=e.target.files[0];if(!file)return;guard(file.size<=2000000,'Backup exceeds the 2 MB prototype limit.');const v=JSON.parse(await file.text());validate(v);pending=v;notice='Check the replacement scope before restoring.';error=false;}catch(e){notice=e instanceof SyntaxError?'Invalid JSON. Your records are unchanged.':e.message;error=true;}render();};
render();
