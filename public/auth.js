'use strict';
let authConfig=null,captchaInstance=null,captchaReady=null,captchaPending=null,authMode='login',authChallenge='',bindChallenge='',captchaTicket=null;
async function initAuth(config){
    authConfig=config;$('authSetupNotice').textContent=config&&!config.sms_ready?'短信注册、找回暂未开通；已有账户可使用密码登录。':'';$('captchaBox').hidden=!config?.captcha_enabled;if(!config?.captcha_enabled)return;
    window.AliyunCaptchaConfig={region:config.region,prefix:config.prefix};
    captchaReady=new Promise((resolve,reject)=>{
        let settled=false;
        const deadline=setTimeout(()=>finish(Error('人机验证码初始化超时：请查看浏览器网络中 AliyunCaptcha.js 和 captcha 请求，以及控制台的 CSP 拦截信息')),30000);
        function finish(error){if(settled)return;settled=true;clearTimeout(deadline);error?reject(error):resolve();}
        const script=document.createElement('script');script.src='https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';script.onerror=()=>finish(Error('人机验证码主脚本加载失败：请检查网络、浏览器扩展及 CSP 限制'));
        script.onload=()=>{try{if(typeof window.initAliyunCaptcha!=='function')throw Error();window.initAliyunCaptcha({SceneId:config.scene,mode:'embed',element:'#captcha-element',button:'#captchaLaunch',server:['captcha-esa-open.aliyuncs.com','captcha-esa-open-b.aliyuncs.com'],timeout:10000,slideStyle:{width:320,height:40},getInstance(instance){if(settled)return;captchaInstance=instance;setTimeout(()=>{finish();$('captchaStatus').textContent='请点击下方组件完成人机验证';},2100);},onError(info){const code=String(info?.code||'UNKNOWN').replace(/[^A-Za-z0-9_.-]/g,'').slice(0,100);captchaTicket=null;const error=Error('人机验证码初始化或资源加载失败，错误码：'+code+'。请核对身份标、场景 ID，并查看浏览器网络中的失败请求');console.warn('ESA CAPTCHA 初始化/资源错误码：',code);finish(error);if(captchaPending){captchaPending.reject(error);captchaPending=null;}},success(param){captchaTicket={param,created:Date.now()};$('captchaStatus').textContent='验证已完成，请在60秒内提交；每次操作需重新验证';if(captchaPending){captchaPending.resolve(param);captchaPending=null;}},fail(){captchaTicket=null;$('captchaStatus').textContent='验证未通过，请重新点击';if(captchaPending){captchaPending.reject(Error('人机验证未通过，请重试'));captchaPending=null;}}});}catch(e){finish(Error('人机验证码初始化调用失败，请查看浏览器控制台并核对身份标与场景'));}};
        document.head.append(script);
    });captchaReady.catch(()=>{});
}
function resetCaptcha(){
    captchaTicket=null;if(captchaInstance)captchaInstance.refresh();
    if(authConfig?.captcha_enabled)$('captchaStatus').textContent='请点击下方组件完成人机验证';
}
function mountCaptcha(where){
    const box=$('captchaBox'),target=$(where==='account'?'phoneForm':'loginForm');
    if(box.parentElement!==target){target.append(box);resetCaptcha();}
}
async function captchaToken(){
    if(!authConfig?.captcha_enabled)return null;await captchaReady;
    if(!captchaTicket){$('captchaBox').scrollIntoView({behavior:'smooth',block:'center'});throw Error('请先点击表单下方的人机验证组件，再提交本次操作');}
    if(Date.now()-captchaTicket.created>=60000){resetCaptcha();throw Error('人机验证已过期，请重新点击后提交');}
    const token=captchaTicket.param;captchaTicket=null;return token;
}
async function authApi(action,body){
    const token=await captchaToken(),headers={'Content-Type':'application/json','X-CSRF-Token':csrf};if(token)headers['captcha-verify-param']=token;
    let r;try{r=await fetch('/login?action='+encodeURIComponent(action),{method:'POST',headers,body:JSON.stringify(body),credentials:'same-origin'});}finally{if(token)resetCaptcha();}
    const verify=r.headers.get('X-Captcha-Verify-Code');if(authConfig?.captcha_enabled&&verify!=='T001')throw Error(verify?'ESA验证码未通过：'+verify:'未收到ESA验签结果（HTTP '+r.status+'）：请检查 campus.example.com/login 的 POST 验签规则，以及是否有规则移除 X-Captcha-Verify-Code 响应头');
    let j;try{j=await r.json();}catch{throw Error('认证接口异常：HTTP '+r.status+'，请检查/login路由和PHP配置');}if(!r.ok)throw Error(j.error||'认证失败');return j;
}
function setAuthMode(mode){
    authMode=mode;authChallenge='';resetCaptcha();const f=$('loginForm');$('loginError').textContent='';f.querySelector('h1').textContent={login:'登录监控台',register:'注册独立账户',recover:'找回登录密码'}[mode];f.querySelector('.primary').textContent={login:'登录',register:'创建账户',recover:'重置密码'}[mode];
    $('authUsername').hidden=mode==='recover';f.username.required=mode!=='recover';$('authSmsFields').hidden=mode==='login';f.phone.required=f.code.required=mode!=='login';f.password.autocomplete=mode==='login'?'current-password':'new-password';$('authToggle').textContent=mode==='register'?'返回密码登录':'注册新账户';$('authBack').hidden=mode==='login';$('authHint').textContent=mode==='login'?'使用用户名和密码登录。':'手机号验证码10分钟有效，每次操作都需人机验证。';
}
function countdown(button,seconds){const original=button.textContent;button.disabled=true;let left=seconds;button.textContent=left+'秒后重发';const timer=setInterval(()=>{left--;button.textContent=left+'秒后重发';if(left<=0){clearInterval(timer);button.disabled=false;button.textContent=original;}},1000);}
$('authToggle').onclick=()=>setAuthMode(authMode==='register'?'login':'register');$('authForgot').onclick=()=>setAuthMode('recover');$('authBack').onclick=()=>setAuthMode('login');
$('sendAuthCode').onclick=async()=>{const b=$('sendAuthCode');b.disabled=true;try{if(!authConfig?.sms_ready)throw Error('短信功能尚未完成接入，请联系管理员');const j=await authApi('send_sms',{phone:$('loginForm').phone.value,purpose:authMode==='recover'?'recover':'register'});authChallenge=j.challenge;message('loginError',j.message);countdown(b,60);}catch(e){message('loginError',e);b.disabled=false;}};
$('loginForm').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('.primary');b.disabled=true;try{const body={...Object.fromEntries(new FormData(e.target)),challenge:authChallenge},j=await authApi(authMode,body);if(authMode==='recover'){e.target.reset();setAuthMode('login');message('loginError',j.message);return;}csrf=j.csrf;currentUser=j.user;e.target.reset();showDashboard();await loadMeters();}catch(e){message('loginError',e);}finally{b.disabled=false;}};
$('sendBindCode').onclick=async()=>{const b=$('sendBindCode');b.disabled=true;try{const j=await authApi('send_sms',{phone:$('phoneForm').phone.value,purpose:'bind'});bindChallenge=j.challenge;message('phoneMessage',j.message);countdown(b,60);}catch(e){message('phoneMessage',e);b.disabled=false;}};
$('phoneForm').onsubmit=async e=>{e.preventDefault();const b=e.target.querySelector('.primary');b.disabled=true;try{const j=await authApi('bind_phone',{...Object.fromEntries(new FormData(e.target)),challenge:bindChallenge});e.target.reset();message('phoneMessage',j.message);await loadMeters();}catch(e){message('phoneMessage',e);}finally{b.disabled=false;}};
