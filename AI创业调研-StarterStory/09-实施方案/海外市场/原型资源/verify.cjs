const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{const browser=await chromium.launch({headless:true,channel:'chrome'}),results=[];try{
 for(const code of (process.env.PROJECTS||'B16,B10,B05,C01,B12,B18,B17,C13,C16,C18').split(',')){
  console.log('Testing '+code);
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.setDefaultTimeout(8000);
  const dir=fs.readdirSync(path.join(root,'项目方案')).find(n=>n.startsWith(code+'-'));await page.goto('file://'+path.join(root,'项目方案',dir,'07-方案与详细设计/原型/index.html'));
  const b=n=>page.getByRole('button',{name:n,exact:true,includeHidden:true});const click=async n=>{await b(n).waitFor({state:'attached'});if(!await b(n).isVisible())await page.locator('footer summary').click();await b(n).click();};
  const form=(k)=>page.locator('[data-field="'+k+'"]');const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('overseas-design-v1-'+JSON.parse(document.getElementById('config').textContent).code)));
  const say=async text=>{await page.locator('#notice').filter({hasText:text}).waitFor();};
  const tab=async n=>click(n);
  const download=async n=>{const waiting=page.waitForEvent('download');await click(n);const d=await waiting;return fs.readFileSync(await d.path(),'utf8');};
  await form('title').fill('First '+code);
  const name=await page.evaluate(()=>JSON.parse(document.getElementById('config').textContent).noun);
  // Objects remain separate; a new object is reachable for workspace-owner roles.
  {
   await click('New '+name);await form('title').fill('Second '+code);await page.locator('[data-record="0"]').click();assert.equal(await form('title').inputValue(),'First '+code);
  }
  if(code==='B16'){
   await form('title').fill('');await click('Send for confirmation');await say('required');await form('title').fill('First B16');await click('Send for confirmation');await click('View as Customer');assert.equal(await page.locator('[data-record]').count(),0);await click('Confirm this version');await say('Read the current');await page.getByLabel('I have read this version carefully').check();await click('Confirm this version');await click('View as Studio');await click('Record print check');assert.equal((await state()).records[0].status,'checked');await click('Create new version');assert.equal(await b('Record print check').isDisabled(),true);await click('Send for confirmation');await click('View as Customer');await page.getByLabel('Revision notes').fill('Move the date');await click('Request changes');await click('View as Studio');await click('Create new version');assert.equal((await state()).records[0].history.length,2);
  }
  if(['B10','B05','C01','B17'].includes(code)){
   await click('Submit brief');const roles=await page.evaluate(()=>JSON.parse(document.getElementById('config').textContent).roles);await click('View as '+roles[1]);await click('Deliver reviewed version');await say('Check the source');await page.locator('#reviewed').check();await click('Deliver reviewed version');await click('View as '+roles[0]);await page.getByLabel('Revision notes').fill('Please review the wording');await click('Request revision');await click('View as '+roles[1]);if(code==='C01')await form('draft').fill('Revised truthful experience');await page.locator('#reviewed').check();await click('Deliver reviewed version');await click('View as '+roles[0]);if(code==='C01'){await click('Accept this version');await say('checked the facts');await page.locator('#reviewed').check();}await click(code==='B17'?'Record receipt':code==='B05'?'Accept sample scope':'Accept this version');const x=(await state()).records[0];assert.equal(x.deliveries.length,2);assert.equal(x.status,'accepted');if(code==='B10')assert.equal(x.result.net,1212000);if(code==='B17')assert.equal(x.result.total,36000);
   await tab('Deliveries');const result=await download(code==='C01'?'Download resume text':'Download result record');assert.ok(result.includes(code==='C01'?'Revised truthful experience':'prototype'));await tab('Work');await click('Edit source & resubmit');assert.equal((await state()).records[0].deliveries.length,2);
  }
  if(code==='B12'){
   await click('Reserve equipment');await page.locator('[data-record="1"]').click();await form('equipment').fill('SPK-001 ');await click('Reserve equipment');await say('overlapping');await page.locator('[data-record="0"]').click();await click('Check out');await click('Record return');assert.ok(await b('Check out').isDisabled());await click('Mark damaged');await click('Repair complete');await click('Inspection passed');assert.equal((await state()).records[0].status,'ready');await page.locator('[data-record="1"]').click();await form('start').fill('2026-09-27T09:00');await form('end').fill('2026-09-27T18:00');await click('Reserve equipment');await page.getByLabel('Revision notes').fill('Event cancelled');await click('Cancel reservation');assert.equal((await state()).records[1].status,'cancelled');await page.locator('[data-record="0"]').click();
  }
  if(code==='B18'){
   await click('Open monthly checklist');await click('View as Client');assert.equal(await page.locator('[data-record]').count(),0);await page.locator('[data-upload="0"]').setInputFiles({name:'statement.pdf',mimeType:'application/pdf',buffer:Buffer.from('sample')});assert.equal((await state()).records[0].items[0].status,'Pending review');await click('View as Bookkeeper');await click('Return Bank statement');await say('Explain why');await page.locator('[data-itemnote="0"]').fill('Wrong month');await click('Return Bank statement');await click('View as Client');await page.locator('[data-upload="0"]').setInputFiles({name:'correct.pdf',mimeType:'application/pdf',buffer:Buffer.from('sample')});await click('View as Bookkeeper');await click('Approve Bank statement');await page.locator('[data-itemnote="1"]').fill('No business receipts this period, confirmed by client');await click('Not applicable: Business receipts');assert.ok((await page.locator('main').innerText()).includes('Complete after review'));
  }
  if(code==='C13'){
   await click('Compare selected files');await say('both sides');await page.locator('#leftFiles').setInputFiles([{name:'a.txt',mimeType:'text/plain',buffer:Buffer.from('same')},{name:'b.txt',mimeType:'text/plain',buffer:Buffer.from('different')}]);await page.locator('#rightFiles').setInputFiles([{name:'copy.txt',mimeType:'text/plain',buffer:Buffer.from('same')},{name:'right-only.txt',mimeType:'text/plain',buffer:Buffer.from('unique')}]);await click('Compare selected files');await say('Comparison complete');const rows=(await state()).records[0].result.rows;assert.ok(rows.some(x=>x.status==='Byte-confirmed identical'));assert.ok(rows.some(x=>x.status.includes('No equal content')));assert.ok(rows.some(x=>x.right==='right-only.txt'&&x.status.includes('No equal content')));
  }
  if(code==='C16'){
   await form('stock').fill('1.2');await click('Update shopping list');await say('half skeins');await form('stock').fill('1');await click('Update shopping list');await click('Record purchase');assert.equal((await state()).records[0].stock,1.5);assert.ok(await b('Record purchase').isDisabled());await click('Undo last stock change');assert.equal((await state()).records[0].stock,1);await page.locator('#known').uncheck();await click('Update shopping list');assert.ok(await b('Record purchase').isDisabled());
  }
  if(code==='C18'){
   await click('Save plant & check date');await click('Record inspection');assert.ok(!(await state()).records[0].events.some(x=>x.text.startsWith('Watering')));await click('Record watering');await click('Undo last care event');assert.ok((await state()).records[0].events.some(x=>x.text.startsWith('Correction')));
  }
  // Each example exports its own browser data, validates imports, then restores.
  if(['B10','B05','C01'].includes(code)){const roles=await page.evaluate(()=>JSON.parse(document.getElementById('config').textContent).roles);await click('View as '+roles[1]);}
  const backup=await download('Export browser backup');const saved=JSON.parse(backup);assert.equal(saved.code,code);
  if(code==='C13'){const bad=JSON.parse(backup);bad.records[0].result={};await page.locator('#restoreFile').setInputFiles({name:'shape.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});await say('Invalid result');assert.equal((await state()).records[0].result.rows.length,saved.records[0].result.rows.length);}
  if(code==='C16'){const bad=JSON.parse(backup);bad.records[0].last={stock:-1};await page.locator('#restoreFile').setInputFiles({name:'stock.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});await say('invalid stock');}

  await page.locator('#restoreFile').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{bad')});await say('Invalid JSON');assert.equal((await state()).records.length,saved.records.length);
  await page.locator('#restoreFile').setInputFiles({name:'backup.json',mimeType:'application/json',buffer:Buffer.from(backup)});await say('replacement scope');await click('Confirm restore');await say('restored');await page.reload();assert.equal((await state()).records.length,saved.records.length);
  assert.deepEqual(errors,[]);await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'mobile overflow');
  if(['B16','C16','B18'].includes(code))await page.screenshot({path:'/tmp/overseas-'+code+'-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});if(['B16','B10','C18'].includes(code))await page.screenshot({path:'/tmp/overseas-'+code+'-desktop.png',fullPage:true});
  results.push({code,result:'passed',checks:'main flow, error paths, history, download, backup validation/restore, reload, mobile layout, no page errors'});await page.close();
 }
 fs.writeFileSync('/tmp/overseas-prototype-results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
