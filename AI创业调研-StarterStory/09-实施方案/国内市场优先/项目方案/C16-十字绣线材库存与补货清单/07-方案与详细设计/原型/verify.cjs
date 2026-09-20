// Browser acceptance: task inputs must change later results; regression coverage for critical branches.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const base=__dirname;
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||"chrome"});
 const results=[];
 for(const code of ['C16']){
  const page=await browser.newPage({viewport:{width:1280,height:900}});page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));

  await page.goto('file://'+path.join(base,'index.html'));
  try{
   assert.ok(await page.locator('main input:not([type=checkbox]),main textarea,main select').count()>0,'用户必须能输入任务资料，而非只读样例');
   const fill=async(n,v)=>page.getByLabel(n,{exact:true}).fill(v);
   const click=async(n)=>{const b=page.getByRole('button',{name:n,exact:true});if(!await b.isVisible()&&await page.locator('#tools').count())await page.locator('#tools summary').click();await b.click();};
   const text=async(t)=>assert.ok((await page.locator('main').innerText()).includes(t),'缺少结果：'+t);
   if(code==='B16'){
    await fill('订单名称','秋季活动单');await click('保存并发送确认');await click('查看客户入口');
    await fill('修改意见','标题改为秋日');await click('退回修改');await click('返回工作台');
    await fill('校样内容','秋日活动 / 9月20日');await click('保存并发送确认');await click('查看客户入口');
    await click('确认这一版');await click('返回工作台');await text('客户已确认');await click('登记印前复核');await text('已完成印前复核');const dl=page.waitForEvent('download');await click('下载确认记录');const file=await dl;const exported=JSON.parse(fs.readFileSync(await file.path(),'utf8'));assert.equal(exported.版本历史.length,2,'导出必须保留被退回的旧版和确认版');
    await click('修改并创建新版');await text('等待重新确认');assert.equal(await page.getByRole('button',{name:'登记印前复核',exact:true}).isEnabled(),false);
   }else if(code==='B10'){
    await fill('本期实收（元）','1000');await fill('本期退款（元）','100');await fill('广告支出（元）','50');await click('提交本期资料');await click('查看交付人员工作台');await click('生成样报');await click('查看客户入口');await text('900.00');await text('50.00');await click('确认本期报告');await text('已验收');assert.equal(await page.getByRole('button',{name:'提交本期资料',exact:true}).isEnabled(),false,'不能通过重复提交清除已验收报告');
   }else if(code==='B05'){
    await fill('培训主题','秋季饮品');await fill('教材内容','本轮教材：桂花拿铁使用鲜奶。');await click('提交出题需求');assert.equal(await page.getByRole('button',{name:'提交出题需求',exact:true}).isEnabled(),false,'重复提交不可清空交付状态');await click('查看审题工作台');await text('本轮教材：桂花拿铁使用鲜奶。');await fill('题目','桂花拿铁使用哪种奶？');await fill('正确答案','鲜奶');await fill('教材出处','本轮教材');await click('交付已审样题');await click('查看客户入口');await fill('修订意见','请调整措辞');await click('退回修订');await text('等待修订');await click('查看审题工作台');await click('交付已审样题');await click('查看客户入口');await click('确认样题口径');await text('样题已确认');
   }else if(code==='B12'){
    await click('登记归还');await text('待检查');assert.equal(await page.getByRole('button',{name:'确认下一单出库',exact:true}).isEnabled(),false);await click('检查完成，恢复可租');assert.equal(await page.getByLabel('器材编号与名称',{exact:true}).isEnabled(),false,'检查后的可租状态必须绑定原器材');await click('确认下一单出库');await text('已出库');
   }else if(code==='B18'){
    await fill('资料文件名','9月资料.pdf');await click('提交资料');await text('等待审核');await click('查看记账员工作台');await fill('退回原因','请补本期附件');await click('退回补充');await click('查看客户入口');await text('请补本期附件');await click('提交资料');await click('查看记账员工作台');await click('审核通过');await text('尚有资料待提交或审核');await click('查看客户入口');await page.getByLabel('资料项目',{exact:true}).selectOption('1');await click('提交资料');await click('查看记账员工作台');await click('审核通过');await text('本期资料已齐');
   }else if(code==='B17'){
    await fill('变更事项','房间A新增插座');await fill('数量','3');await fill('单价（元）','100');await click('提交办公室复核');await click('查看办公室工作台');await click('审核并生成申请包');await text('300.00');await click('登记接收');await text('已接收，待外部审批');
   }else if(code==='C01'){
    await fill('目标岗位','跨境运营');await fill('真实经历','每周整理广告搜索词，由主管审核');await click('提交给顾问');await click('查看顾问工作台');await click('交付审阅稿');await click('查看求职者入口');await text('每周整理广告搜索词');await fill('修改意见','保留主管审核说明');await click('请求修订');await text('等待顾问修订');await click('查看顾问工作台');await click('交付审阅稿');await click('查看求职者入口');await click('确认终稿');await text('终稿已确认');const dl=page.waitForEvent('download');await click('下载简历文本');const file=await dl;assert.ok(file.suggestedFilename().endsWith('.txt'),'下载简历文本应得到可直接阅读的文本');assert.ok(fs.readFileSync(await file.path(),'utf8').includes('每周整理广告搜索词'));
   }else if(code==='C13'){
    await fill('报告名称','家庭照片核对');await click('扫描示例目录');await text('内容一致');await text('待重新连接');await click('重新连接并复核');await text('复核完成');
   }else if(code==='C16'){
    await fill('现有库存（束）','1');await fill('项目需求（束）','1.5');await click('生成缺线清单');await text('需购买 0.5 束');await click('确认已买并入库');await text('库存 1.5 束');await click('撤销本次入库');await text('库存 1 束');await fill('实际用量（束）','2');await click('登记实际耗用');await text('库存 1 束');assert.ok((await page.locator('#notice').innerText()).includes('不超过库存'));const dl=page.waitForEvent('download');await click('下载备份');const file=await dl;const backupPath=await file.path();await fill('实际用量（束）','0.5');await click('登记实际耗用');await text('库存 0.5 束');await page.locator('#restoreFile').setInputFiles({name:'backup.json',mimeType:'application/json',buffer:fs.readFileSync(backupPath)});await click('确认恢复');await text('库存 1 束');await page.locator('#restoreFile').setInputFiles({name:'broken.json',mimeType:'application/json',buffer:Buffer.from('{broken')});await page.locator('#notice').filter({hasText:'备份文件无法读取'}).waitFor();await text('库存 1 束');assert.ok((await page.locator('#notice').innerText()).includes('备份文件无法读取'),'损坏文件需使用用户可理解的提示');
   }else if(code==='C18'){
    await fill('植物名称','窗边龟背竹');await click('保存植物');await click('记录检查完成');await text('检查完成');assert.ok(!(await page.locator('#history').innerText()).includes('已浇水'));await click('记录已浇水');await text('已浇水');
   }
   assert.deepEqual(errors,[]);
   await page.setViewportSize({width:390,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'手机布局横向溢出');
   await page.screenshot({path:'/tmp/user-prototype-'+code+'-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});await page.screenshot({path:'/tmp/user-prototype-'+code+'-desktop.png',fullPage:true});results.push({code,status:'PASS'});
  }catch(e){results.push({code,status:'FAIL',reason:e.message,labels:await page.locator('main label').allTextContents(),view:await page.locator('#roleName').textContent()});}
  await page.close();
 }
 await browser.close();console.log(JSON.stringify(results,null,2));if(results.some(x=>x.status==='FAIL'))process.exitCode=1;
})();
