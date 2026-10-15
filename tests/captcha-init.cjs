const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(require('path').join(__dirname,'../public/auth.js'),'utf8');
async function run(mode){
  let options,script,timers=[],warnings=[],elements={};
  const context={console:{warn:(...x)=>warnings.push(x)},window:{},Error,
    $:id=>elements[id]??=( {} ),
    document:{createElement:()=>script={},head:{append:s=>{
      if(mode==='script-error')s.onerror();
      else if(mode!=='timeout')s.onload();
    }}},
    setTimeout:(fn,ms)=>{const timer={fn,ms,active:true};timers.push(timer);return timer;},
    clearTimeout:t=>{t.active=false;}
  };
  context.window.initAliyunCaptcha=o=>{
    options=o;
    if(mode==='sdk-error')o.onError({code:'IllegalScene',msg:'private data'});
    else o.getInstance({show(){},refresh(){}});
  };
  vm.createContext(context);vm.runInContext(source,context);
  await vm.runInContext("initAuth({captcha_enabled:true,sms_ready:true,region:'cn',prefix:'',scene:''})",context);
  if(mode==='success')timers.find(t=>t.ms===2100).fn();
  if(mode==='timeout')timers.find(t=>t.ms===30000).fn();
  if(mode==='success'){
    await vm.runInContext('captchaReady',context);
    assert.equal(options.mode,'embed');assert.equal(options.timeout,10000);
    assert.equal(timers.find(t=>t.ms===30000).active,false);
    vm.runInContext('captchaPending={resolve:()=>{},reject:()=>{}}',context);
    options.onError({code:'RESOURCE_ERROR'});
    assert.equal(vm.runInContext('captchaPending',context),null);
    context.csrf='test-csrf';elements.captchaBox.scrollIntoView=()=>{};
    await assert.rejects(vm.runInContext('captchaToken()',context),/请先点击/);
    options.success('mock-ticket');
    assert.equal(await vm.runInContext('captchaToken()',context),'mock-ticket');
    await assert.rejects(vm.runInContext('captchaToken()',context),/请先点击/);
    vm.runInContext("captchaTicket={param:'expired',created:Date.now()-60001}",context);
    await assert.rejects(vm.runInContext('captchaToken()',context),/已过期/);
    let sent=0;
    context.fetch=async (url,init)=>{sent++;assert.equal(url,'/login?action=login');assert.equal(init.headers['captcha-verify-param'],'mock-ticket');return {ok:true,status:200,headers:{get:()=>null},json:async()=>({ok:true})};};
    options.success('mock-ticket');
    await assert.rejects(vm.runInContext("authApi('login',{})",context),/未收到ESA验签结果/);
    assert.equal(sent,1);
    await assert.rejects(vm.runInContext("authApi('login',{})",context),/请先点击/);
    assert.equal(sent,1);
    context.fetch=async()=>({ok:true,status:200,headers:{get:()=> 'T001'},json:async()=>({ok:true})});
    options.success('mock-ticket');
    assert.equal((await vm.runInContext("authApi('login',{})",context)).ok,true);
  }else{
    await assert.rejects(vm.runInContext('captchaReady',context),mode==='sdk-error'?/IllegalScene/:mode==='timeout'?/初始化超时/:/主脚本加载失败/);
  }
  assert(!JSON.stringify(warnings).includes('private data'));
}
(async()=>{for(const mode of ['success','sdk-error','script-error','timeout'])await run(mode);console.log('PASS: captcha ready, SDK error, script failure, timeout, pending rejection, no private error data');})().catch(e=>{console.error(e);process.exit(1)});
