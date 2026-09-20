const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:"chrome"});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file://'+path.join(__dirname,'index.html'));
 await page.evaluate(()=>localStorage.clear());await page.reload();
 const click=async n=>{const b=page.getByRole('button',{name:n,exact:true,includeHidden:true});await b.waitFor({state:'attached'});if(!await b.isVisible())await page.locator('#tools').evaluate(e=>e.open=true);await b.click();};
 const fill=async(id,v)=>page.locator('#'+id).fill(v);
 const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('os-b04-independent-v1')));
 const notice=()=>page.locator('#notice').innerText();
 await click('Submit brief');assert.match(await notice(),/permission|Confirm/);
 await fill('episodeTitle','Temporary edit');await click('02 Excerpts');assert.match(await notice(),/discard/);await click('Discard unsaved brief');
 await page.locator('#rights').check();await click('Submit brief');await click('View as editor');
 await fill('end','121');await click('Save excerpt');assert.match(await notice(),/30–90/);assert.equal(await page.locator('#end').inputValue(),'121');await click('Discard unsaved fields');
 await page.locator('#checked').check();await click('03 Review');assert.match(await notice(),/discard/);await click('Save excerpt');
 for(let i=1;i<3;i++){await page.locator('[data-clip="'+i+'"]').click();await page.locator('#checked').check();await click('Save excerpt');}
 await page.locator('#sourceFile').setInputFiles({name:'bad.txt',mimeType:'text/plain',buffer:Buffer.from('bad')});assert.match(await notice(),/video file/);
 await page.locator('#sourceFile').setInputFiles({name:'test.mp4',mimeType:'video/mp4',buffer:Buffer.from('not a decodable video')});await click('Play selected range');assert.match(await notice(),/not ready|shorter/);
 await click('Send three excerpts for review');assert.equal((await state()).episodes[0].phase,'review');
 await page.screenshot({path:'/tmp/os-b04-review-desktop.png',fullPage:true});
 await click('View as client');await page.locator('[data-clip="0"]').click();await click('Approve this excerpt');assert.match(await notice(),/Read/);await page.locator('#read').check();await click('Approve this excerpt');
 await page.locator('[data-clip="1"]').click();await click('Request changes');assert.match(await notice(),/Explain/);await fill('feedback','Retain the small-pilot qualification.');await click('Request changes');
 await page.locator('[data-clip="2"]').click();await page.locator('#read').check();await click('Approve this excerpt');assert.equal((await state()).episodes[0].phase,'review');
 await click('View as editor');await click('Start new revision');let e=(await state()).episodes[0];assert.equal(e.version,2);assert.ok(e.clips.every(c=>c.status==='draft'&&!c.checked));assert.equal(e.history[0].decisions[0].status,'approved');assert.equal(e.history[0].decisions[1].status,'changes');
 for(let i=0;i<3;i++){await page.locator('[data-clip="'+i+'"]').click();if(i===1)await fill('post','One small pilot: results have not been tested across other teams.');await page.locator('#checked').check();await click('Save excerpt');}
 await click('Send three excerpts for review');await click('View as client');
 for(let i=0;i<3;i++){await page.locator('[data-clip="'+i+'"]').click();await page.locator('#read').check();await click('Approve this excerpt');}
 assert.equal((await state()).episodes[0].phase,'approved');await click('04 Handoff');await click('View as editor');await click('Prepare review manifest');
 const download=async n=>{const p=page.waitForEvent('download');await click(n);const d=await p;return fs.readFile(await d.path(),'utf8');};
 const manifest=JSON.parse(await download('Download review manifest'));assert.equal(manifest.status,'review-approved-media-not-rendered');assert.equal(manifest.version,2);
 for(let i=1;i<=3;i++)assert.match(await download('Clip '+i+' SRT'),/00:00:00,000 -->/);
 assert.match(await download('Clip 2 copy'),/One small pilot/);assert.match(await download('Download chapter notes'),/Opening/);
 await page.screenshot({path:'/tmp/os-b04-handoff-desktop.png',fullPage:true});
 const backup=await download('Download browser backup');const original=JSON.stringify(await state());
 await page.locator('#restore').setInputFiles({name:'broken.json',mimeType:'application/json',buffer:Buffer.from('{')});assert.match(await notice(),/Invalid JSON/);assert.equal(JSON.stringify(await state()),original);
 const malformed=JSON.parse(backup);malformed.episodes[0].manifest.clips[0].quote='Changed after approval';await page.locator('#restore').setInputFiles({name:'bad-state.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(malformed))});assert.match(await notice(),/Invalid manifest/);assert.equal(JSON.stringify(await state()),original);
 await click('＋ New episode');assert.equal((await state()).episodes.length,2);await click('View as client');await fill('episodeTitle','Second episode');await fill('studioName','Second studio');await fill('sourceRef','second.mp4');await page.locator('#rights').check();await click('Submit brief');
 await page.locator('#restore').setInputFiles({name:'saved.json',mimeType:'application/json',buffer:Buffer.from(backup)});await click('Confirm replacement');assert.equal((await state()).episodes.length,1);assert.equal((await state()).episodes[0].phase,'manifest');
 await page.reload();await click('04 Handoff');assert.match(await page.locator('#main').innerText(),/This is not a completed delivery/);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/os-b04-mobile.png',fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 assert.deepEqual(errors,[]);console.log('PASS: browser workflow, validation, revision history, downloads, backup rejection/restore, persistence, mobile overflow and page errors.');
 console.log('Screenshots: /tmp/os-b04-review-desktop.png /tmp/os-b04-handoff-desktop.png /tmp/os-b04-mobile.png');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
