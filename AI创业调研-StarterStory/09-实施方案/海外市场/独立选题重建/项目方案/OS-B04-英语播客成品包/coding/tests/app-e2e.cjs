const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.API_URL||'http://127.0.0.1:8000/api',stamp=Date.now().toString(36),password='Mobile-integration-password!';
 async function api(path,token,method='GET',body){const r=await fetch(base+path,{method,headers:{...(token?{Authorization:`Bearer ${token}`} :{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const value=await r.json();assert.equal(r.status,200,JSON.stringify(value));return value;}
 const editor=await api('/auth/register',null,'POST',{name:'Mobile Editor',email:`app-ed-${stamp}@example.com`,password,workspaceName:'Mobile Studio'});
 async function invite(s){const i=await api('/invites',editor.token,'POST',{email:`app-${s}-${stamp}@example.com`});return api('/auth/accept-invite',null,'POST',{token:i.token,name:`Mobile Client ${s}`,password});}
 const a=await invite('a'),b=await invite('b');
 let ep=await api('/episodes',a.token,'POST',{title:`Mobile story ${stamp}`,brand:'Northline',source:'Private authorized source',duration:180,prohibitedClaims:'None',notes:'Keep context',glossary:'Pilot'});
 ep=await api(`/episodes/${ep.id}/brief`,a.token,'PUT',{...ep,rights:true});ep=await api(`/episodes/${ep.id}/actions/submit`,a.token,'POST',{revision:ep.revision});ep=await api(`/episodes/${ep.id}/actions/ready`,editor.token,'POST',{revision:ep.revision,sourceUsable:true,scopeConfirmed:true,editorQualified:true,paymentPathConfirmed:true});
 for(let i=1;i<=3;i++)ep=await api(`/episodes/${ep.id}/clips/clip-${i}`,editor.token,'PUT',{revision:ep.revision,title:`Mobile clip ${i}`,start:(i-1)*40,end:(i-1)*40+30,quote:'A small pilot produced useful feedback.',context:'Not generalizable.',post:`Post ${i}`,checked:true});
 ep=await api(`/episodes/${ep.id}/chapters`,editor.token,'PUT',{revision:ep.revision,chapters:'00:00 Intro\n00:40 Pilot\n01:20 Context'});
 const browser=await chromium.launch({headless:true,channel:'chrome'});let page;
 try{
 page=await browser.newPage({viewport:{width:390,height:844}});const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(process.env.APP_URL||'http://localhost:8081');
 const button=name=>name==='Sign in'?page.getByRole('button',{name,exact:true}).last():page.getByRole('button',{name,exact:true});
 async function submit(btn,part){const [r]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/')&&r.url().includes(part)&&['POST','PUT'].includes(r.request().method())),btn.click()]);assert.equal(r.status(),200,await r.text());await page.waitForTimeout(100);}
 async function login(email){await page.getByLabel('Email',{exact:true}).fill(email);await page.getByLabel('Password (10+ characters)').fill(password);await submit(button('Sign in'),'/auth/login');}
 await login(editor.user.email);await page.getByText(ep.title,{exact:true}).click();await page.getByLabel('Social post',{exact:true}).nth(1).fill('Unsent sibling edit must survive');await submit(button('Save clip').nth(0),'/clips/clip-1');assert.equal(await page.getByLabel('Social post',{exact:true}).nth(1).inputValue(),'Unsent sibling edit must survive');assert.ok(await button('Send complete package for review').isDisabled());
 const cancel=page.waitForEvent('dialog').then(d=>d.dismiss());await button('Refresh episode').click();await cancel;assert.equal(await page.getByLabel('Social post',{exact:true}).nth(1).inputValue(),'Unsent sibling edit must survive');
 await submit(button('Save clip').nth(1),'/clips/clip-2');await submit(button('Send complete package for review'),'/actions/send');
 await submit(button('Sign out'),'/auth/logout');await login(a.user.email);await page.getByText(ep.title,{exact:true}).click();await page.getByRole('switch',{name:'I read the entire quote, context and post for this version.',exact:true}).first().check();await submit(button('Approve this clip').first(),'/actions/approve');
 await page.screenshot({path:'/tmp/episode-desk-app-review.png',fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 // Expire exactly the current test session, then sign in as a different client.
 const token=await page.evaluate(()=>sessionStorage.getItem('episode-token'));await api('/auth/logout',token,'POST',{});await button('Refresh episode').click();await button('Sign in').waitFor();await login(b.user.email);await page.getByText('Your next episode starts here',{exact:true}).waitFor();assert.equal(await page.getByText(ep.title,{exact:true}).count(),0);assert.deepEqual(errors,[]);
 console.log('PASS: Expo web mobile layout, real API login, sibling draft retention, send gating, cancel refresh, review approval, expired-session account isolation.');
 }catch(e){if(page)await page.screenshot({path:'/tmp/episode-desk-app-fail.png',fullPage:true});throw e;}finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1;});
