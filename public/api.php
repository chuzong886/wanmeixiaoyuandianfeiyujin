<?php
declare(strict_types=1);
require dirname(__DIR__).'/app/core.php';require ROOT.'/app/install.php';require ROOT.'/app/monitor.php';require ROOT.'/app/dashboard.php';require ROOT.'/app/capture.php';require ROOT.'/app/auth.php';
header('Content-Type: application/json; charset=utf-8');header('Cache-Control: no-store');header('X-Content-Type-Options: nosniff');
function reply(array $v,int $status=200): never {http_response_code($status);echo json_value($v);exit;}
try{
    $installed=file_exists(ROOT.'/storage/config.php');session_name('campus_php_session');session_set_cookie_params(['httponly'=>true,'secure'=>$installed?(bool)config()['secure_cookie']:(($_SERVER['HTTPS']??'')==='on'),'samesite'=>'Strict','path'=>'/']);session_start();$_SESSION['csrf']??=bin2hex(random_bytes(32));$action=$_GET['action']??'session';$post=$_SERVER['REQUEST_METHOD']==='POST';
    if($action==='session'){if(!$installed)setup_token();$u=null;if($installed){try{$u=user();}catch(AppError $e){}}reply(['installed'=>$installed,'csrf'=>$_SESSION['csrf'],'user'=>$u,'registration_enabled'=>$installed?(bool)setting('registration_enabled',true):false,'auth'=>$installed?auth_public():null,'checks'=>$installed?[]:environment_checks()]);}
    $body=[];if($post){if(!hash_equals($_SESSION['csrf'],$_SERVER['HTTP_X_CSRF_TOKEN']??''))throw new AppError('页面已过期，请刷新后再试',403);try{$body=json_decode(file_get_contents('php://input'),true,64,JSON_THROW_ON_ERROR);}catch(Throwable $e){throw new AppError('请求内容不是有效 JSON');}if(!is_array($body))throw new AppError('请求内容无效');}
    if($action==='install'&&$post){install_app($body);reply(['ok'=>true,'message'=>'安装完成，请登录']);}
    if(!$installed)throw new AppError('请先完成安装',503);
    if($post&&in_array($action,['login','register','recover','send_sms','bind_phone'],true)){
        if(!defined('CAMPUS_AUTH_ROUTE'))throw new AppError('认证接口已迁移，请刷新页面后在/login操作',410);
        $actor=null;if($action==='bind_phone'||($action==='send_sms'&&($body['purpose']??'')==='bind'))$actor=user();
        captcha_gate($action!=='login');
        if($action==='login')reply(auth_login($body));if($action==='register')reply(auth_register($body));if($action==='recover')reply(auth_recover($body));if($action==='send_sms')reply(send_phone_code($body,$actor));if($action==='bind_phone')reply(auth_bind_phone($body,$actor));
    }
    $u=user();
    if($post&&$action==='parse_capture'){$source=(string)($body['source']??'');$fields=$body['fields']??[];if(!is_array($fields))throw new AppError('字段格式无效');reply(parse_capture($source,$fields));}
    if($post&&$action==='logout'){session_destroy();reply(['ok'=>true]);}
    if($post&&$action==='password'){$old=(string)($body['current_password']??'');$new=(string)($body['password']??'');$row=one('SELECT password_hash FROM ca_users WHERE id=?',[$u['id']]);if(!password_verify($old,$row['password_hash'])||strlen($new)<12||strlen($new)>200)throw new AppError('原密码错误或新密码长度无效');sql('UPDATE ca_users SET password_hash=?,session_version=session_version+1 WHERE id=?',[password_hash($new,PASSWORD_DEFAULT),$u['id']]);$_SESSION['version']++;session_regenerate_id(true);reply(['ok'=>true]);}
    if($action==='meters'&&!$post)reply(['meters'=>array_map('public_meter',sql('SELECT * FROM ca_meters WHERE user_id=? ORDER BY id',[$u['id']])),'user'=>$u]);
    if($action==='save_meter'&&$post){$id=save_meter($body,$u);reply(['ok'=>true,'id'=>$id]);}
    if($action==='save_notification'&&$post){save_account_channel($body,$u);reply(['ok'=>true,'message'=>'此通道与模板已独立保存']);}
    if($action==='clear_notification'&&$post){save_account_channel($body,$u,true);reply(['ok'=>true,'message'=>'此通道地址已清除并关闭']);}
    if($action==='snapshot'&&!$post){$m=owner_meter((int)($_GET['meter_id']??0),$u);reply(dashboard($m,max(7,min(30,(int)($_GET['days']??14))),max(0,(int)($_GET['history_revision']??0))));}
    if($action==='query'&&$post){$m=owner_meter((int)($body['meter_id']??0),$u);$reason=manual_reason($m,microtime(true));if($reason!=='')throw new AppError($reason,409);session_write_close();$attempt=perform_query($m,microtime(true),'manual');if(!$attempt)throw new AppError('已有查询执行或配置已改变',409);$r=one('SELECT state,error FROM ca_attempts WHERE id=?',[$attempt]);reply(['ok'=>$r['state']==='ok','rooms'=>state((int)$m['id'],'rooms',[]),'message'=>$r['state']==='ok'?'查询完成，电量与预测已更新':($r['error']??'查询失败')]);}
    if($action==='account_channels'&&!$post)reply(['channels'=>account_channel_public((int)$u['id']),'migration_notes'=>setting('channel_migration:'.$u['id'],[]),'tests'=>sql('SELECT kind,created,state,details,request_id FROM ca_channel_tests WHERE user_id=? ORDER BY id DESC LIMIT 20',[$u['id']])]);
    if($action==='legacy_channels'&&!$post){$rows=sql("SELECT c.*,m.label FROM ca_channels c JOIN ca_meters m ON m.id=c.meter_id WHERE m.user_id=? AND c.secret<>''",[$u['id']]);foreach($rows as &$row){$raw=unseal($row['secret']);$row['config']=in_array($row['kind'],['daily','emergency'],true)?['url'=>$raw]:json_decode($raw,true);unset($row['config']['password'],$row['secret']);}reply(['channels'=>$rows]);}
    if($action==='test_channel'&&$post)reply(test_account_channel($body,$u));
    if(str_starts_with($action,'admin_')){if($u['role']!=='admin')throw new AppError('仅管理员可以访问',403);
        if($action==='admin_status'&&!$post)reply(['registration_enabled'=>(bool)setting('registration_enabled',true),'max_meters_per_user'=>(int)setting('max_meters_per_user',5),'users'=>sql('SELECT u.id,u.username,u.phone,u.role,u.enabled,u.created,(SELECT COUNT(*) FROM ca_meters m WHERE m.user_id=u.id) AS meter_count FROM ca_users u ORDER BY u.id'),'heartbeat'=>(float)setting('worker_heartbeat',0)]);
        if($action==='admin_add_user'&&$post){admin_add_user($body);reply(['ok'=>true]);}
        if($action==='admin_edit_user'&&$post){admin_edit_user($body,$u);reply(['ok'=>true]);}
        if($action==='admin_auth'&&!$post){$o=auth_options();unset($o['sms_secret'],$o['edge_secret']);reply($o+['sms_configured'=>auth_options()['sms_secret']!=='','edge_configured'=>auth_options()['edge_secret']!=='']);}
        if($action==='admin_auth'&&$post){save_auth_options($body);reply(['ok'=>true,'message'=>'认证配置已保存']);}
        if($action==='admin_settings'&&$post){$n=(int)($body['max_meters_per_user']??5);if($n<1||$n>20)throw new AppError('每用户项目上限为1～20');set_setting('registration_enabled',(bool)($body['registration_enabled']??false));set_setting('max_meters_per_user',$n);reply(['ok'=>true]);}
        if($action==='admin_user'&&$post){$id=(int)($body['id']??0);if($id===(int)$u['id'])throw new AppError('不能停用当前管理员');$enabled=(bool)($body['enabled']??false);transaction(function()use($id,$enabled){$target=one('SELECT id,role FROM ca_users WHERE id=? FOR UPDATE',[$id]);if(!$target||$target['role']==='admin')throw new AppError('不能通过此入口修改管理员');sql('UPDATE ca_users SET enabled=?,session_version=session_version+1 WHERE id=?',[(int)$enabled,$id]);if(!$enabled){sql("UPDATE ca_jobs j JOIN ca_meters m ON m.id=j.meter_id SET j.state='blocked',j.message='账户已停用' WHERE m.user_id=? AND j.state='queued'",[$id]);sql("UPDATE ca_outbox o JOIN ca_meters m ON m.id=o.meter_id SET o.state='cancelled',o.details='账户已停用' WHERE m.user_id=? AND o.state='queued'",[$id]);}});reply(['ok'=>true]);}
    }
    throw new AppError('请求无效',404);
}catch(AppError $e){reply(['error'=>$e->getMessage()],$e->status);}catch(Throwable $e){reply(['error'=>'服务暂不可用，请检查数据库和运行日志'],503);}
