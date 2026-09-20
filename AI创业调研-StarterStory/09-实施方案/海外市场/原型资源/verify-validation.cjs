'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=__dirname+'/';
const source=fs.readFileSync(root+'app.js','utf8'),products=JSON.parse(fs.readFileSync(root+'products.json','utf8'));
const snippet=source.slice(source.indexOf('function validate(v){'),source.indexOf('function persist(){'));
assert.ok(snippet.startsWith('function validate(v){'));
function record(code,id='one'){return {id,form:Object.fromEntries(products[code].fields.map(f=>[f.key,f.value])),status:'draft',version:1,history:[],deliveries:[],events:[],items:[{name:'Bank statement',status:'Missing'},{name:'Business receipts',status:'Missing'}],result:null,stock:null,need:null,last:null,known:true,reason:'',reviewed:false};}
function savedStock(id='one'){const x=record('C16',id);x.status='saved';x.stock=1;x.need=1.5;return x;}
let count=0;
function test(name,code,records,accept){const db={schema:1,code,records},before=JSON.stringify(db);const context=vm.createContext({code,cfg:products[code],db});vm.runInContext(snippet,context);let message=null;try{vm.runInContext('validate(db)',context);}catch(e){message=e.message;}assert.equal(message===null,accept,name+': '+message);assert.equal(JSON.stringify(db),before,name+': validation changed data');count++;console.log('PASS '+name+(message?' [rejected: '+message+']':' [accepted]'));}
for(const code of Object.keys(products))test(code+' initial draft',code,[record(code)],true);
let x=record('C16');x.form.title='';test('C16 new blank draft','C16',[x],true);
x=savedStock();x.stock=100000.5;x.need=100001;x.form.stock='100000.5';x.form.need='100001';test('C16 100000.5 valid quantity','C16',[x],true);
x=savedStock();x.stock=1000000;x.need=1000000;test('C16 upper boundary','C16',[x],true);
x=savedStock();x.stock=1000000.5;test('C16 above upper boundary','C16',[x],false);
x=savedStock();x.last={stock:-1};test('C16 negative undo quantity','C16',[x],false);
x=savedStock();x.last={};test('C16 damaged undo shape','C16',[x],false);
x=savedStock();x.stock=-1;test('C16 negative current quantity','C16',[x],false);
x=savedStock();x.stock='1';test('C16 string current quantity','C16',[x],false);
x=savedStock();x.need=null;test('C16 saved missing need','C16',[x],false);
x=savedStock();x.stock=null;test('C16 known without stock','C16',[x],false);
x=savedStock();x.known=false;test('C16 unknown with stock','C16',[x],false);
x=savedStock();x.known=false;x.stock=null;test('C16 valid unknown stock','C16',[x],true);
let a=savedStock('a'),b=savedStock('b');b.form.title+=' ';test('C16 duplicate saved colour with space','C16',[a,b],false);
a=savedStock('a');b=record('C16','b');test('C16 saved plus same-colour draft','C16',[a,b],true);
a=record('C16','a');b=record('C16','b');a.form.title='';b.form.title='';test('C16 multiple blank drafts','C16',[a,b],true);
x=record('C13');x.result={};test('C13 damaged result','C13',[x],false);
x=record('C13');x.status='scanned';x.result={method:'Example',time:'2026-09-20',rows:[{left:'a',right:'b',status:'Example only'}]};test('C13 valid report','C13',[x],true);
x=record('B16');x.status='sent';test('B16 sent without history','B16',[x],false);
x=record('B16');x.status='sent';x.history=[{version:1,content:x.form.content,status:'sent'}];test('B16 sent with current history','B16',[x],true);
x=record('B16');x.status='sent';x.version=2;x.history=[{version:1,content:x.form.content,status:'sent'}];test('B16 sent missing current version','B16',[x],false);
console.log('TOTAL '+count+' passed; all validations preserve input.');
