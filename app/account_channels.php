<?php
declare(strict_types=1);
function account_channel(int $uid,string $kind): ?array {return one('SELECT * FROM ca_user_channels WHERE user_id=? AND kind=?',[$uid,$kind]);}
function channel_for_meter(array $m,string $kind): ?array {return account_channel((int)$m['user_id'],$kind);}
function account_channel_public(int $uid): array {
    $out=[];
    foreach(['daily','emergency','webhook','smtp'] as $kind){
        $ch=account_channel($uid,$kind);$v=['enabled'=>(bool)($ch['enabled']??false),'configured'=>($ch['secret']??'')!=='','mode'=>$ch['mode']??'fixed','templates'=>channel_templates($kind,$ch?json_decode($ch['templates'],true):null)];
        if(in_array($kind,['daily','emergency'],true))$v['url']=$ch?unseal($ch['secret']):'';
        elseif($ch&&$ch['secret']!==''){$extra=json_decode(unseal($ch['secret']),true);unset($extra['password']);$v+=$extra;}
        $out[$kind]=$v;
    }return $out;
}
function cancel_account_channel(int $uid,string $kind): void {sql("UPDATE ca_outbox o JOIN ca_meters m ON m.id=o.meter_id SET o.state='cancelled',o.details='账户通道已改变' WHERE m.user_id=? AND o.channel=? AND o.state='queued'",[$uid,$kind]);}
function save_account_channel(array $body,array $u,bool $clear=false): void {
    transaction(function()use($body,$u,$clear){
        one('SELECT id FROM ca_users WHERE id=? FOR UPDATE',[$u['id']]);$kind=(string)($body['kind']??'');if(!in_array($kind,['daily','emergency','webhook','smtp'],true))throw new AppError('通道无效');
        $old=account_channel((int)$u['id'],$kind);$i=$body['config']??[];if(!is_array($i))throw new AppError('配置无效');$secret=$old['secret']??'';$enabled=(bool)($i['enabled']??false);$mode=$old['mode']??'fixed';$tpl=channel_templates($kind,$old?json_decode($old['templates'],true):null);
        if($clear){$secret='';$enabled=false;}
        elseif(in_array($kind,['daily','emergency'],true)){
            $url=trim((string)($i['url']??''));validate_secret_url($kind,$url);if($url!=='')$secret=seal($url);$mode=(string)($i['mode']??'fixed');if(!in_array($mode,['fixed','mapped'],true))throw new AppError('模板模式无效');
            $input=$body['templates']??$tpl;if(!is_array($input))throw new AppError('模板字段无效');$tpl=[];foreach(template_defaults($kind) as $k=>$default){$v=(string)($input[$k]??$default);if(mb_strlen($v)>500)throw new AppError('每个字段最多500字');preg_match_all('/\{([^{}]+)\}/',$v,$matches);foreach($matches[1] as $token)if(!in_array($token,['label','time','balance','summary','voice','event','level','remaining_hours','empty_at'],true))throw new AppError('模板变量无效');$tpl[$k]=$v;}
        }else{
            $v=$secret!==''?json_decode(unseal($secret),true):[];
            if($kind==='webhook'){$url=trim((string)($i['url']??''));if($url!=='')$v=['url'=>$url];if($v)webhook_url($v['url']);}
            else{foreach(['host','port','security','username','from','to'] as $k)if(isset($i[$k]))$v[$k]=$i[$k];if(($i['password']??'')!=='')$v['password']=$i['password'];if(!empty($v['host']))smtp_validate($v);else $v=[];}
            $secret=$v?seal(json_value($v)):'';
        }
        if($enabled&&$secret==='')throw new AppError('启用前请填写此通道的配置');
        sql('INSERT INTO ca_user_channels(user_id,kind,enabled,secret,mode,templates) VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled),secret=VALUES(secret),mode=VALUES(mode),templates=VALUES(templates),config_version=config_version+1',[$u['id'],$kind,(int)$enabled,$secret,$mode,json_value($tpl)]);
        cancel_account_channel((int)$u['id'],$kind);
    });
}
function spug_result(int $http,array $reply): array {
    if($http===200&&in_array($reply['code']??null,[200,'200'],true))return ['accepted','Spug已受理；公众号/电话是否送达请查看Spug发送日志',preg_match('/^[A-Za-z0-9_-]{1,128}$/D',(string)($reply['request_id']??''))?(string)$reply['request_id']:null];
    $business=is_scalar($reply['code']??null)?substr(preg_replace('/[^A-Za-z0-9_-]/','',(string)$reply['code']),0,24):'missing';
    return ['rejected','Spug未受理：HTTP '.$http.'，业务码 '.$business.'；请检查模板、推送对象、额度、IP白名单',null];
}
function test_account_channel(array $body,array $u,?callable $sender=null): array {
    $kind=(string)($body['kind']??'');if(!in_array($kind,['daily','emergency','webhook','smtp'],true))throw new AppError('通道无效');
    $id=(int)($body['meter_id']??0);$m=$id?owner_meter($id,$u):null;$label=$m['label']??($u['username'].'账户');$now=microtime(true);
    $ch=transaction(function()use($u,$kind,$now){one('SELECT id FROM ca_users WHERE id=? FOR UPDATE',[$u['id']]);$last=(float)setting('channel_test:'.$u['id'],0);if($now-$last<10)throw new AppError('请间隔10秒再测试',429);$ch=account_channel((int)$u['id'],$kind);if(!$ch||$ch['secret']==='')throw new AppError('请先保存此账户的通道');set_setting('channel_test:'.$u['id'],$now);return $ch;});
    $payload=['event_name'=>'【测试】'.$label.'通知测试','event_time'=>date('Y-m-d H:i:s'),'summary_short'=>'这是主动测试消息，不代表真实低电量','account_label'=>'【测试】'.$label,'balance_display'=>'测试电量','voice_text'=>'这是通知测试，请忽略'];$state='unknown';$detail='结果未知，不自动重发';$requestId=null;
    $log=sql('INSERT INTO ca_channel_tests(user_id,kind,created,state,details) VALUES (?,?,?,?,?)',[$u['id'],$kind,$now,'sending','主动测试']);$logId=(int)db()->lastInsertId();session_write_close();
    try{
        if(in_array($kind,['webhook','smtp'],true)){$wire=['title'=>'校园电量通知测试','type'=>'test','test'=>true,'meter'=>$label,'time'=>date(DATE_ATOM),'message'=>'这是主动测试，请忽略'];if($sender)$sender($ch,$wire);else send_extra($ch,$wire);$state='accepted';$detail='接收服务器已受理，请检查接收端';}
        else{$vars=notice_vars('【测试】'.$label,$payload+['alert_level'=>'测试'],$now,'测试电量');$wire=$ch['mode']==='mapped'?$payload:render_fields(channel_templates($kind,json_decode($ch['templates'],true)),$vars);[$http,$reply]=$sender?$sender($ch,$wire):http_post(unseal($ch['secret']),$wire);[$state,$detail,$requestId]=spug_result($http,$reply);}
    }catch(AppError $e){$detail=$e->getMessage();}catch(Throwable $e){$detail='测试执行失败，请检查PHP扩展、网络及服务器日志';}
    sql('UPDATE ca_channel_tests SET state=?,details=?,request_id=? WHERE id=?',[$state,$detail,$requestId,$logId]);return ['ok'=>$state==='accepted','message'=>$detail,'state'=>$state,'request_id'=>$requestId];
}
