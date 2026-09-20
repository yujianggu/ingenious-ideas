// V3 acceptance: isolate records, preserve previous deliveries and restore complete collections.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=__dirname;
const specs={B16:['订单','订单名称','订单甲','订单乙'],B10:['报告周期','报告周期','第一周','第二周'],B05:['培训任务','培训主题','饮品培训','服务培训'],B12:['器材','器材编号与名称','A-001','B-002'],B18:['客户资料清单','客户名称','青禾','山川'],B17:['变更事项','变更事项','增加插座','移动灯位'],C01:['简历任务','目标岗位','运营岗位','广告岗位'],C13:['核对任务','报告名称','家庭照片','旅行照片'],C16:['色号','色号','001','002'],C18:['植物','植物名称','龟背竹','绿萝']};
(async()=>{const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'chrome'});const results=[];
for(const [code,[noun,label,first,second]] of Object.entries(specs).filter(([key])=>key==='B05')){
 const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(4000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('file://'+path.join(base,'index.html'));
 const button=n=>page.getByRole('button',{name:n,exact:true});
 const click=async n=>{if(!await button(n).isVisible()&&await page.locator('#tools').count())await page.locator('#tools summary').click();await button(n).click();};
 const fill=(n,v)=>page.getByLabel(n,{exact:true}).fill(v);
 const notice=t=>page.locator('#notice').filter({hasText:t}).waitFor();
 const select=()=>page.getByLabel('选择'+noun,{exact:true});
 const download=async name=>{const pending=page.waitForEvent('download');await click(name);const file=await pending;return fs.readFileSync(await file.path());};
 try{
  if(code==='B18')await click('查看记账员工作台');
  assert.equal(await button('新建'+noun).count(),1,'用户应能新建下一条业务记录，而不是重置并丢失已有任务');
  await fill(label,first);
  if(code==='C16'){await fill('现有库存（束）','1');await fill('项目需求（束）','1.5');await click('生成缺线清单');}
  if(code==='C18'){await click('保存植物');await click('记录检查完成');}
  const firstId=await select().inputValue();
  await click('新建'+noun);await fill(label,second);const secondId=await select().inputValue();assert.notEqual(firstId,secondId);
  if(code==='C16'){await fill('现有库存（束）','2');await fill('项目需求（束）','3');await click('生成缺线清单');}
  if(code==='C18'){await click('保存植物');await click('记录已浇水');}
  await select().selectOption(firstId);assert.equal(await page.getByLabel(label,{exact:true}).inputValue(),first,'切换后应恢复原记录自己的输入');
  if(code==='C18'){await fill('植物名称','尚未保存的名字');assert.ok((await select().locator('option:checked').textContent()).includes(first),'列表应使用已保存植物名');await fill('植物名称',first);assert.ok((await page.locator('#history').innerText()).includes('检查完成'));assert.ok(!(await page.locator('#history').innerText()).includes('已浇水'),'植物之间不能串历史');}
  if(code==='C16'){assert.ok((await page.locator('main').innerText()).includes('库存 1 束'));await page.getByLabel('库存情况',{exact:true}).selectOption('unknown');assert.equal(await button('确认已买并入库').isEnabled(),false,'已知改待核后不能继续使用旧计算入库');assert.equal(await button('登记实际耗用').isEnabled(),false);await page.getByLabel('库存情况',{exact:true}).selectOption('known');}
  await page.reload();assert.equal(await page.getByLabel(label,{exact:true}).inputValue(),first,'刷新应保留当前记录');assert.equal(await select().locator('option').count(),2);
  if(code==='B16'){await fill('确认客户','客户甲');await fill('校样内容','甲校样');await click('保存并发送确认');await click('查看客户入口');assert.equal(await select().count(),0,'外部客户入口不能列出其他订单');await click('返回工作台');}
  if(code==='B18'){await fill('资料期间','2026-09');await click('保存客户与期间');await click('查看客户入口');assert.equal(await select().count(),0,'外部客户不可切换别人的资料清单');assert.ok((await page.locator('main').innerText()).includes('青禾'));await click('查看记账员工作台');}
  if(code==='B10'){
   await fill('本期实收（元）','1000');await fill('本期退款（元）','100');await fill('广告支出（元）','50');await click('提交本期资料');await click('查看交付人员工作台');await click('生成样报');await click('查看客户入口');await click('确认本期报告');await click('修改提交资料');await fill('本期实收（元）','2000');await click('提交本期资料');await click('查看交付人员工作台');await click('生成样报');await click('查看客户入口');
   const exportData=JSON.parse(await download('下载结果摘要'));assert.equal(exportData.历次交付.length,2);assert.equal(exportData.历次交付[0].net,900);assert.equal(exportData.历次交付[1].net,1900,'修改资料重交不能覆盖原来的900报告');
  }
  if(code==='C16'||code==='C18'){
   const backup=await download('下载备份');const payload=JSON.parse(backup);assert.equal(payload.version,3);assert.equal(payload.records.length,2,'备份必须包含整个列表，不能只导出当前记录');
   await click('新建'+noun);await fill(label,code==='C16'?'003':'月季');
   await page.locator('#restoreFile').setInputFiles({name:'collection.json',mimeType:'application/json',buffer:backup});await notice('备份已读取');await click('确认恢复');assert.equal(await select().locator('option').count(),2,'恢复必须正确替换整个集合');
   await select().selectOption(secondId);assert.equal(await page.getByLabel(label,{exact:true}).inputValue(),second);
   const bad={...payload,records:payload.records.map((r,i)=>i===1?{...r,id:payload.records[0].id}:r)};await page.locator('#restoreFile').setInputFiles({name:'duplicate.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});await notice('备份格式');assert.equal(await select().locator('option').count(),2,'损坏集合不得覆盖原数据');
  }
  if(code==='C16'){
   const legacy={version:2,code:'C16',data:{stock:1,need:1.5,color:'001',events:[]}};await page.locator('#restoreFile').setInputFiles({name:'legacy-conflict.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(legacy))});await notice('色号已存在');assert.equal(await page.getByLabel('色号',{exact:true}).inputValue(),'002','冲突备份不能把当前色号改成重复项');
   await click('新建色号');await fill('色号','004');await fill('项目需求（束）','2');await page.getByLabel('库存情况',{exact:true}).selectOption('unknown');await click('生成缺线清单');assert.ok((await page.locator('main').innerText()).includes('数量待核'));assert.equal(await button('确认已买并入库').isEnabled(),false,'未知库存不应被当成0自动采购');
  }
  assert.deepEqual(errors,[]);await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:'/tmp/workspace-'+code+'-mobile.png',fullPage:true});results.push({code,status:'PASS'});
 }catch(e){results.push({code,status:'FAIL',reason:e.message});}
 await page.close();
}await browser.close();console.log(JSON.stringify(results,null,2));if(results.some(r=>r.status==='FAIL'))process.exitCode=1;})();
