const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../public/app.js'),'utf8');
const start=source.indexOf("$('query').onclick="),end=source.indexOf("$('historyRevision').onchange=",start);
assert(start>=0&&end>start);
let request,refreshed=false;
const elements={query:{disabled:false},historyRevision:{value:'1'},meterForm:{elements:{namedItem:()=>({value:'9'})},roomverify:{value:''}},notice:{textContent:''}};
const context={currentId:9,currentUser:{id:4},historyRevision:1,busy:false,logged:true,
  $:id=>elements[id],roomChoices:()=>{},api:async(action,body)=>{request={action,body};return {message:'mock current query complete',rooms:[]}},
  refresh:async()=>{refreshed=true;}
};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
(async()=>{
  await elements.query.onclick();
  assert.equal(request.action,'query');assert.equal(request.body.meter_id,9);assert(!('history_revision' in request.body));
  assert.equal(context.historyRevision,0);assert.equal(elements.historyRevision.value,'0');
  assert(refreshed);assert.equal(elements.notice.textContent,'mock current query complete');assert.equal(context.busy,false);
  console.log('PASS: continue from archive queries current configuration, clears history view and refreshes current results');
})().catch(e=>{console.error(e);process.exit(1)});
