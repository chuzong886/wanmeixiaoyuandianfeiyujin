<?php
declare(strict_types=1);
function auth_options(): array {return array_replace(['captcha_enabled'=>false,'prefix'=>'','scene'=>'','region'=>'cn','edge_secret'=>'','sms_secret'=>'','sms_daily_cap'=>100],setting('auth_options',[]));}
function auth_public(): array {$v=auth_options();return ['captcha_enabled'=>(bool)$v['captcha_enabled'],'prefix'=>$v['prefix'],'scene'=>$v['scene'],'region'=>$v['region'],'sms_ready'=>$v['sms_secret']!==''&&$v['captcha_enabled']&&$v['edge_secret']!==''];}
function phone_value(mixed $value): string {$v=trim((string)$value);if(!preg_match('/^1[3-9][0-9]{9}$/D',$v))throw new AppError('请填写11位大陆手机号');return $v;}
function password_value(mixed $value): string {$v=(string)$value;if(strlen($v)<12||strlen($v)>200)throw new AppError('密码须为12～200字节');return $v;}
function otp_hash(string $id,string $code): string {return hash_hmac('sha256',$id.':'.$code,config()['app_key']);}
function auth_ip_hash(): string {return hash_hmac('sha256',$_SERVER['REMOTE_ADDR']??'',config()['app_key']);}
function auth_session_hash(): string {return hash_hmac('sha256',session_id(),config()['app_key']);}
function captcha_gate(bool $required=false): void {
    $o=auth_options();if(!$o['captcha_enabled']){if($required)throw new AppError('短信功能需先完成ESA验证码接入，请联系管理员',503);return;}
    // ESA must overwrite this origin-auth header after enforcing its POST CAPTCHA rule.
    // A browser success callback or a user-supplied verification code is never trusted.
    $expected=$o['edge_secret']!==''?unseal($o['edge_secret']):'';
    if($expected===''||!hash_equals($expected,(string)($_SERVER['HTTP_X_CAMPUS_EDGE']??''))||trim((string)($_SERVER['HTTP_CAPTCHA_VERIFY_PARAM']??''))==='')throw new AppError('ESA验证码或回源验证未通过，请检查/login的POST规则',403);
}
function save_auth_options(array $body): void {
    $o=auth_options();foreach(['prefix','scene','region'] as $k)if(isset($body[$k]))$o[$k]=trim((string)$body[$k]);
    if(!preg_match('/^esa-[A-Za-z0-9_-]{4,80}$/D',$o['prefix'])||!preg_match('/^[A-Za-z0-9_-]{4,80}$/D',$o['scene'])||!in_array($o['region'],['cn','sgp'],true))throw new AppError('验证码身份标或场景参数无效');
    if(($body['sms_url']??'')!==''){$url=trim((string)$body['sms_url']);if(!preg_match('#^https://push\.spug\.cc/sms/[A-Za-z0-9_-]{4,128}$#D',$url))throw new AppError('短信地址须为Spug官方/sms/接口，不含查询参数');$o['sms_secret']=seal($url);}
    if(($body['edge_secret']??'')!==''){$secret=(string)$body['edge_secret'];if(strlen($secret)<32||strlen($secret)>128||!preg_match('/^[A-Za-z0-9_-]+$/D',$secret))throw new AppError('回源密钥须为32～128位字母数字或下划线');$o['edge_secret']=seal($secret);}
    $o['sms_daily_cap']=max(1,min(1000,(int)($body['sms_daily_cap']??$o['sms_daily_cap'])));$o['captcha_enabled']=(bool)($body['captcha_enabled']??false);
    if($o['captcha_enabled']&&$o['edge_secret']==='')throw new AppError('请先配置ESA回源密钥；不要仅凭前端通过启用验证码');set_setting('auth_options',$o);
}
function sms_transport(string $url,array $params): array {
    $c=curl_init($url.'?'.http_build_query($params));curl_setopt_array($c,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_TIMEOUT=>20,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS]);$raw=curl_exec($c);$http=(int)curl_getinfo($c,CURLINFO_HTTP_CODE);curl_close($c);
    if($raw===false||strlen($raw)>65536)throw new AppError('短信请求结果未知，请稍后重试');$r=json_decode($raw,true);if(!is_array($r))throw new AppError('短信平台响应异常');return [$http,$r];
}
function send_phone_code(array $body,?array $actor=null,?callable $send=null): array {
    $phone=phone_value($body['phone']??'');$purpose=(string)($body['purpose']??'');if(!in_array($purpose,['register','recover','bind'],true))throw new AppError('验证码用途无效');if($purpose==='bind'&&!$actor)throw new AppError('请登录',401);
    if($purpose==='register'&&!setting('registration_enabled',true))throw new AppError('管理员已关闭注册',403);$o=auth_options();if($o['sms_secret']==='')throw new AppError('管理员尚未配置短信验证码接口',503);
    $target=one('SELECT id,enabled FROM ca_users WHERE phone=?',[$phone]);$uid=$purpose==='bind'?(int)$actor['id']:($purpose==='recover'&&$target?(int)$target['id']:null);$now=microtime(true);$code=(string)random_int(100000,999999);$id=bin2hex(random_bytes(24));$ip=auth_ip_hash();$sess=auth_session_hash();
    transaction(function()use($phone,$purpose,$uid,$now,$code,$id,$ip,$sess,$o){
        one("SELECT value FROM ca_settings WHERE name='auth_mutex' FOR UPDATE");
        if(one('SELECT id FROM ca_otp WHERE phone=? AND created>?',[$phone,$now-60]))throw new AppError('请间隔60秒再获取验证码',429);
        $n=(int)one('SELECT COUNT(*) n FROM ca_otp WHERE phone=? AND created>?',[$phone,$now-3600])['n'];$ipn=(int)one('SELECT COUNT(*) n FROM ca_otp WHERE ip_hash=? AND created>?',[$ip,$now-3600])['n'];$daily=(int)one('SELECT COUNT(*) n FROM ca_otp WHERE created>?',[strtotime(date('Y-m-d'))])['n'];if($n>=5||$ipn>=20||$daily>=$o['sms_daily_cap'])throw new AppError('验证码请求过多，请稍后再试',429);
        sql('DELETE FROM ca_otp WHERE created<?',[$now-7*86400]);sql("UPDATE ca_otp SET state='superseded' WHERE phone=? AND purpose=? AND state='accepted'",[$phone,$purpose]);
        sql('INSERT INTO ca_otp(id,phone,purpose,user_id,code_hash,session_hash,ip_hash,created,expires,state) VALUES (?,?,?,?,?,?,?,?,?,?)',[$id,$phone,$purpose,$uid,otp_hash($id,$code),$sess,$ip,$now,$now+600,'sending']);
    });
    // Existing/nonexistent accounts receive the same public response; no SMS for impossible actions.
    $eligible=($purpose==='register'&&!$target)||($purpose==='recover'&&$target&&(int)$target['enabled'])||($purpose==='bind'&&(!$target||(int)$target['id']===$uid));$state='blocked';$requestId=null;
    if($eligible){try{[$http,$r]=$send?$send(unseal($o['sms_secret']),['to'=>$phone,'code'=>$code,'number'=>'10']):sms_transport(unseal($o['sms_secret']),['to'=>$phone,'code'=>$code,'number'=>'10']);[$status,,$requestId]=spug_result($http,$r);$state=$status==='accepted'?'accepted':'rejected';}catch(Throwable $e){$state='unknown';}}
    sql('UPDATE ca_otp SET state=?,request_id=? WHERE id=?',[$state,$requestId,$id]);
    if(in_array($state,['rejected','unknown'],true))throw new AppError($state==='unknown'?'短信请求结果未知，60秒后可重新获取；不会自动重发':'短信平台未受理，请联系管理员检查短信额度和模板',502);
    return ['ok'=>true,'challenge'=>$id,'expires_in'=>600,'message'=>'若手机号符合此操作条件，验证码请求已受理，请留意短信'];
}
// Called inside the same transaction as registration/recovery/binding; failures are returned so attempts commit.
function verify_phone_code(array $body,string $purpose,?int $uid=null): ?string {
    $id=(string)($body['challenge']??'');$code=(string)($body['code']??'');$phone=phone_value($body['phone']??'');$r=one('SELECT * FROM ca_otp WHERE id=? FOR UPDATE',[$id]);
    if(!$r||$r['phone']!==$phone||$r['purpose']!==$purpose||!hash_equals($r['session_hash'],auth_session_hash())||($uid!==null&&(int)$r['user_id']!==$uid)||$r['state']!=='accepted'||microtime(true)>$r['expires']||(int)$r['attempts']>=5)return '验证码已失效，请重新获取';
    sql('UPDATE ca_otp SET attempts=attempts+1 WHERE id=?',[$id]);if(!preg_match('/^[0-9]{6}$/D',$code)||!hash_equals($r['code_hash'],otp_hash($id,$code)))return '验证码错误';
    sql("UPDATE ca_otp SET state='consumed' WHERE id=?",[$id]);return null;
}
function auth_login(array $body): array {
    $name=trim((string)($body['username']??''));$password=(string)($body['password']??'');$ip=auth_ip_hash();
    if(!valid_username($name)||strlen($password)>200)throw new AppError('用户名或密码错误',401);
    $r=transaction(function()use($name,$password,$ip){one("SELECT value FROM ca_settings WHERE name='auth_mutex' FOR UPDATE");if((int)one("SELECT COUNT(*) n FROM ca_auth_attempts WHERE ip_hash=? AND kind='login' AND ts>?",[$ip,microtime(true)-900])['n']>=8)throw new AppError('尝试次数过多，请15分钟后再试',429);$u=one('SELECT * FROM ca_users WHERE username=?',[$name]);$hash=$u['password_hash']??'$2y$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2uheWG/igi.';if(!$u||!password_verify($password,$hash)||!(int)$u['enabled']){sql("INSERT INTO ca_auth_attempts(ip_hash,ts,kind) VALUES (?,?,'login')",[$ip,microtime(true)]);return ['error'=>'用户名或密码错误，或账户已停用'];}sql("DELETE FROM ca_auth_attempts WHERE ip_hash=? AND kind='login'",[$ip]);return $u;});if(isset($r['error']))throw new AppError($r['error'],401);return auth_session($r);
}
function auth_session(array $u): array {session_regenerate_id(true);$_SESSION['uid']=(int)$u['id'];$_SESSION['version']=(int)$u['session_version'];$_SESSION['expires']=time()+8*3600;$_SESSION['csrf']=bin2hex(random_bytes(32));return ['ok'=>true,'csrf'=>$_SESSION['csrf'],'user'=>user()];}
function auth_register(array $body): array {
    if(!setting('registration_enabled',true))throw new AppError('管理员已关闭注册',403);$name=trim((string)($body['username']??''));$pass=password_value($body['password']??'');$phone=phone_value($body['phone']??'');if(!valid_username($name))throw new AppError('用户名须为3～32位字母数字');
    $r=transaction(function()use($body,$name,$pass,$phone){one("SELECT value FROM ca_settings WHERE name='auth_mutex' FOR UPDATE");if(one('SELECT id FROM ca_users WHERE username=? OR phone=?',[$name,$phone]))return ['error'=>'用户名或手机号不可用'];if($error=verify_phone_code($body,'register'))return ['error'=>$error];sql('INSERT INTO ca_users(username,password_hash,phone,created) VALUES (?,?,?,?)',[$name,password_hash($pass,PASSWORD_DEFAULT),$phone,microtime(true)]);return ['id'=>(int)db()->lastInsertId(),'session_version'=>1];});if(isset($r['error']))throw new AppError($r['error']);return auth_session($r);
}
function auth_recover(array $body): array {
    $phone=phone_value($body['phone']??'');$pass=password_value($body['password']??'');$r=transaction(function()use($body,$phone,$pass){$u=one('SELECT * FROM ca_users WHERE phone=? FOR UPDATE',[$phone]);if(!$u||!(int)$u['enabled'])return '手机号或验证码无效';if($error=verify_phone_code($body,'recover',(int)$u['id']))return $error;sql('UPDATE ca_users SET password_hash=?,session_version=session_version+1 WHERE id=?',[password_hash($pass,PASSWORD_DEFAULT),$u['id']]);return null;});if($r!==null)throw new AppError($r);return ['ok'=>true,'message'=>'密码已重置，请使用密码登录'];
}
function auth_bind_phone(array $body,array $u): array {
    $phone=phone_value($body['phone']??'');$pass=(string)($body['current_password']??'');$r=transaction(function()use($body,$u,$phone,$pass){$fresh=one('SELECT password_hash FROM ca_users WHERE id=? FOR UPDATE',[$u['id']]);if(!password_verify($pass,$fresh['password_hash']))return '当前密码错误';if(one('SELECT id FROM ca_users WHERE phone=? AND id<>?',[$phone,$u['id']]))return '此手机号不可用';if($error=verify_phone_code($body,'bind',(int)$u['id']))return $error;sql('UPDATE ca_users SET phone=? WHERE id=?',[$phone,$u['id']]);return null;});if($r!==null)throw new AppError($r);return ['ok'=>true,'message'=>'手机号已更新'];
}
function admin_add_user(array $body): void {
    $name=trim((string)($body['username']??''));$pass=password_value($body['password']??'');$phone=trim((string)($body['phone']??''));if(!valid_username($name))throw new AppError('用户名须为3～32位字母数字');if($phone!=='')$phone=phone_value($phone);
    transaction(function()use($name,$pass,$phone){one("SELECT value FROM ca_settings WHERE name='auth_mutex' FOR UPDATE");if(one('SELECT id FROM ca_users WHERE username=? OR phone=?',[$name,$phone!==''?$phone:null]))throw new AppError('用户名或手机号已被使用');sql('INSERT INTO ca_users(username,password_hash,phone,created) VALUES (?,?,?,?)',[$name,password_hash($pass,PASSWORD_DEFAULT),$phone!==''?$phone:null,microtime(true)]);});
}
function admin_edit_user(array $body,array $actor): void {
    $id=(int)($body['id']??0);if($id===(int)$actor['id'])throw new AppError('修改自己的密码或手机号请使用“我的账户”');
    transaction(function()use($body,$id){$target=one('SELECT * FROM ca_users WHERE id=? FOR UPDATE',[$id]);if(!$target||$target['role']==='admin')throw new AppError('不能从此入口修改管理员');$phone=array_key_exists('phone',$body)?trim((string)$body['phone']):$target['phone'];if($phone!==null&&$phone!=='')$phone=phone_value($phone);if($phone&&one('SELECT id FROM ca_users WHERE phone=? AND id<>?',[$phone,$id]))throw new AppError('手机号已使用');$hash=$target['password_hash'];$changed=($body['password']??'')!=='';if($changed)$hash=password_hash(password_value($body['password']),PASSWORD_DEFAULT);sql('UPDATE ca_users SET phone=?,password_hash=?,session_version=session_version+? WHERE id=?',[$phone?:null,$hash,(int)$changed,$id]);});
}
