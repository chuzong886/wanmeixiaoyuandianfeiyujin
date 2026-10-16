<?php
declare(strict_types=1);
date_default_timezone_set('Asia/Shanghai');
const ROOT = __DIR__.'/..';
final class AppError extends RuntimeException { public function __construct(string $message,public int $status=400){parent::__construct($message);} }
function config(): array { static $c; return $c??=require ROOT.'/storage/config.php'; }
function db(): PDO { static $db; if(!$db){$c=config();$db=connect_db($c); } return $db; }
function connect_db(array $c): PDO { return new PDO("mysql:host={$c['host']};port={$c['port']};dbname={$c['database']};charset=utf8mb4",$c['user'],$c['password'],[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION,PDO::ATTR_EMULATE_PREPARES=>false,PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC,PDO::ATTR_TIMEOUT=>10]); }
function sql(string $text,array $args=[]): array { $q=db()->prepare($text);$q->execute($args);return $q->columnCount()?$q->fetchAll():[]; }
function one(string $text,array $args=[]): ?array { return sql($text,$args)[0]??null; }
function transaction(callable $fn): mixed { $d=db();$d->beginTransaction();try{$r=$fn();$d->commit();return $r;}catch(Throwable $e){if($d->inTransaction())$d->rollBack();throw $e;} }
function json_value(mixed $x): string {return json_encode($x,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);}
function state(int $id,string $key,mixed $default=null): mixed {$r=one('SELECT value FROM ca_state WHERE meter_id=? AND `name`=?',[$id,$key]);return $r?json_decode($r['value'],true,512,JSON_THROW_ON_ERROR):$default;}
function set_state(int $id,string $key,mixed $v): void {sql('INSERT INTO ca_state(meter_id,`name`,value) VALUES (?,?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)',[$id,$key,json_value($v)]);}
function setting(string $key,mixed $default=null): mixed {$r=one('SELECT value FROM ca_settings WHERE `name`=?',[$key]);return $r?json_decode($r['value'],true):$default;}
function set_setting(string $key,mixed $v): void {sql('INSERT INTO ca_settings(`name`,value) VALUES (?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)',[$key,json_value($v)]);}
function seal(string $plain): string {if($plain==='')return ''; $iv=random_bytes(12);$tag='';$raw=openssl_encrypt($plain,'aes-256-gcm',base64_decode(config()['app_key'],true),OPENSSL_RAW_DATA,$iv,$tag);if($raw===false)throw new RuntimeException('Encryption failed');return base64_encode($iv.$tag.$raw);}
function unseal(string $secret): string {if($secret==='')return ''; $raw=base64_decode($secret,true);if($raw===false||strlen($raw)<28)throw new RuntimeException('Invalid secret');$plain=openssl_decrypt(substr($raw,28),'aes-256-gcm',base64_decode(config()['app_key'],true),OPENSSL_RAW_DATA,substr($raw,0,12),substr($raw,12,16));if($plain===false)throw new RuntimeException('Decryption failed');return $plain;}
function event(int $id,float $ts,string $kind,array $details=[]): void {sql('INSERT INTO ca_events(meter_id,ts,kind,details) VALUES (?,?,?,?)',[$id,$ts,$kind,json_value($details)]);}
function default_options(): array {return ['window_start'=>'08:00','window_end'=>'22:00','max_queries'=>8,'min_gap_minutes'=>60,'warning_kwh'=>20,'critical_kwh'=>3,'clear_kwh'=>25,'reserve_kwh'=>3,'warning_hours'=>24,'critical_hours'=>6,'clear_hours'=>36,'guard_multiplier'=>1.25,'reading_tolerance_kwh'=>.2,'anomaly_absolute_kwh'=>.5,'anomaly_relative'=>.3,'fast_half_life_hours'=>6,'slow_half_life_hours'=>48,'phone_cooldown_hours'=>6,'max_emergency_requests'=>2,'report_weekday'=>4,'forecast_notice_enabled'=>false,'forecast_notice_hours'=>12.];}
function options(array $m): array {return array_replace(default_options(),json_decode($m['settings'],true)??[]);}
function bounds(float $now,array $o): array {$day=date('Y-m-d',(int)$now);return [$day,(float)strtotime($day.' '.$o['window_start']),(float)strtotime($day.' '.$o['window_end'])];}
function quota(int $id,string $day): int {return (int)one("SELECT COUNT(*) n FROM ca_attempts WHERE meter_id=? AND day=? AND kind<>'manual'",[$id,$day])['n'];}
function latest(array $m): ?array {return one('SELECT * FROM ca_readings WHERE meter_id=? AND revision=? ORDER BY ts DESC,id DESC LIMIT 1',[$m['id'],$m['revision']]);}
function user(): array {$id=(int)($_SESSION['uid']??0);$u=$id?one('SELECT id,username,role,phone,enabled,session_version FROM ca_users WHERE id=?',[$id]):null;if(!$u||!(int)$u['enabled']||time()>($_SESSION['expires']??0)||(int)$u['session_version']!==($_SESSION['version']??0)){unset($_SESSION['uid']);throw new AppError('请登录',401);}return $u;}
function owner_meter(int $id,array $u,bool $lock=false): array {$m=one('SELECT * FROM ca_meters WHERE id=? AND user_id=?'.($lock?' FOR UPDATE':''),[$id,$u['id']]);if(!$m)throw new AppError('监控项目不存在',404);return $m;}
function valid_username(string $name): bool {return (bool)preg_match('/^[A-Za-z0-9_-]{3,32}$/D',$name);}
function validate_secret_url(string $kind,string $url): void {
    if($url==='')return;
    $p=parse_url($url);if(!$p||($p['scheme']??'')!=='https'||isset($p['user'])||isset($p['pass'])||isset($p['query'])||isset($p['fragment'])||(isset($p['port'])&&$p['port']!==443))throw new AppError('通知地址必须是有效的官方 HTTPS 调用地址');
    $good=in_array($kind,['daily','emergency'],true)?(($p['host']??'')==='push.spug.cc'&&preg_match('#^/send/[A-Za-z0-9_-]{4,128}$#D',$p['path']??'')):false;
    if(!$good)throw new AppError('仅支持 Spug 官方 /send/ 通知地址');
}
function campus_key(string $stamp,string $random): string {if(!preg_match('/^[0-9]{6,16}$/D',$stamp)||!preg_match('/^[A-Z0-9]{10}$/D',$random))throw new AppError('校园加密参数无效');return (((int)$stamp%8)%2?substr($stamp,0,6):substr($stamp,-6)).$random;}
function http_post(string $url,array $payload,bool $form=false,array $headers=[]): array {
    $ch=curl_init($url);$headers[]=$form?'Content-Type: application/x-www-form-urlencoded':'Content-Type: application/json';
    curl_setopt_array($ch,[CURLOPT_POST=>true,CURLOPT_POSTFIELDS=>$form?http_build_query($payload):json_value($payload),CURLOPT_HTTPHEADER=>$headers,CURLOPT_RETURNTRANSFER=>true,CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_TIMEOUT=>25,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS,CURLOPT_MAXREDIRS=>0]);
    $raw=curl_exec($ch);$code=(int)curl_getinfo($ch,CURLINFO_HTTP_CODE);$errno=curl_errno($ch);curl_close($ch);if($raw===false)throw new AppError('网络请求失败，cURL错误码'.$errno.'；请检查DNS、出站网络及CA证书');if(strlen($raw)>2*1024*1024)throw new AppError('响应过大');$body=json_decode($raw,true);if(!is_array($body))throw new AppError('响应不是有效JSON，HTTP '.$code.'；请检查代理或拦截页面');return [$code,$body];
}
function query_campus(array $m): array {
    $stamp=(string)(int)floor(microtime(true)*1000);$alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';$random='';for($i=0;$i<10;$i++)$random.=$alphabet[random_int(0,35)];
    $inner=['cmd'=>'getbindroom','account'=>unseal($m['account_secret']),'timestamp'=>$stamp];$outer=['param'=>json_value($inner),'customercode'=>$m['school_code'],'method'=>'getbindroom','command'=>$m['command']];
    $encrypt=openssl_encrypt(json_value($outer),'aes-128-ecb',campus_key($stamp,$random),0);if($encrypt===false)throw new AppError('校园请求加密失败');
    [$code,$reply]=http_post('https://xqh5.17wanxiao.com/smartWaterAndElectricityService/SWAEEncryptServlet',['encryptData'=>$encrypt,'timestamp'=>$stamp,'randomStr'=>$random,'method'=>'getbindroom'],true,['Origin: https://xqh5.17wanxiao.com','Referer: https://xqh5.17wanxiao.com/userwaterelecmini/index.html','X-Requested-With: com.newcapec.mobile.ncp']);
    if($code!==200)throw new AppError('校园 HTTP 查询失败');
    if(!empty($reply['encryptData'])){$plain=openssl_decrypt((string)$reply['encryptData'],'aes-128-ecb',campus_key((string)($reply['timestamp']??''),(string)($reply['randomStr']??'')),0);$reply=$plain===false?null:json_decode($plain,true);}
    if(!is_array($reply)||strtolower((string)($reply['result_']??''))!=='true'||(isset($reply['code_'])&&(string)$reply['code_']!=='0'))throw new AppError('校园外层业务查询失败');
    $body=$reply['body']??null;if(is_string($body))$body=json_decode($body,true);if(!is_array($body)||(string)($body['result']??'')!=='0'||isset($body['_result']))throw new AppError('校园内部业务查询失败');
    return select_room($body,$m);
}
function select_room(array $body,array $m): array {
    if((string)($body['existflag']??'')==='0')throw new AppError('校园账户未绑定宿舍');$rooms=$body['roomlist']??[$body];if(!is_array($rooms))throw new AppError('房间列表无效');$candidates=[];$available=[];
    foreach($rooms as $r){if(!is_array($r)||empty($r['roomverify']))continue;$available[]=['roomverify'=>(string)$r['roomverify'],'label'=>(string)($r['roomfullname']??$r['roomverify'])];if($m['roomverify']!==''&&(string)$r['roomverify']!==$m['roomverify'])continue;foreach(($r['detaillist']??[]) as $d){if(!is_array($d)||(string)($d['businesstype']??'')!==$m['business_type'])continue;$v=$d['odd']??null;if(!is_numeric($v)||!is_finite((float)$v)||(float)$v<0)throw new AppError('剩余电量不是有效非负数值');$candidates[]=['balance'=>(float)$v,'balance_raw'=>decimal_kwh($v)['raw'],'room'=>(string)($r['roomfullname']??$r['roomverify']),'roomverify'=>(string)$r['roomverify'],'series'=>hash('sha256',$m['school_code'].':'.unseal($m['account_secret']).':'.$r['roomverify'].':'.$m['business_type'])];}}
    if(count($candidates)!==1)return ['selection_required'=>true,'rooms'=>$available];return $candidates[0]+['rooms'=>$available];
}
