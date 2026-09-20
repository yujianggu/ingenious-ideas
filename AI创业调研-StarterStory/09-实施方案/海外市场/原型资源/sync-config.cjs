// Keep file://-compatible inline configs aligned with the shared product catalogue.
const fs=require('fs'),path=require('path');
const base=path.resolve(__dirname,'..'),specs=JSON.parse(fs.readFileSync(path.join(__dirname,'products.json'),'utf8'));
for(const [code,spec] of Object.entries(specs)){
 const dir=fs.readdirSync(path.join(base,'项目方案')).find(n=>n.startsWith(code+'-')&&fs.statSync(path.join(base,'项目方案',n)).isDirectory());
 const file=path.join(base,'项目方案',dir,'07-方案与详细设计','原型','index.html');
 const text=fs.readFileSync(file,'utf8'),pattern=/<script id="config" type="application\/json">[\s\S]*?<\/script>/;
 if(!pattern.test(text))throw Error('Missing configuration: '+code);
 const config=JSON.stringify(spec).replace(/<\//g,'<\\/');
 fs.writeFileSync(file,text.replace(pattern,()=>'<script id="config" type="application/json">'+config+'</script>'));
}
console.log('Updated 10 inline product configurations.');
