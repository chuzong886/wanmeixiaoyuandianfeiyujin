<?php
declare(strict_types=1);
// Explicit opt-in, loopback-only disposable database. No external queries or sends.
if(getenv('CAMPUS_TEST_DB')!=='1')exit("Set CAMPUS_TEST_DB=1 and CAMPUS_TEST_DB_PORT for an isolated local database.\n");
require dirname(__DIR__).'/app/monitor.php';require ROOT.'/app/dashboard.php';require ROOT.'/app/migrate.php';
if(file_exists(ROOT.'/storage/config.php'))exit("Refusing an installed checkout\n");
set_error_handler(static function($severity,$message,$file,$line){throw new ErrorException($message,0,$severity,$file,$line);});
$port=(int)getenv('CAMPUS_TEST_DB_PORT');if($port<1024||$port>65535)exit("Invalid isolated test port\n");
$root=new PDO('mysql:host=127.0.0.1;port='.$port.';charset=utf8mb4',getenv('CAMPUS_TEST_DB_USER')?:'root',getenv('CAMPUS_TEST_DB_PASSWORD')?:'',[PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
$dbName='campus_regression_'.bin2hex(random_bytes(6));$n=0;
function verify301(bool $ok,string $label): void {global $n;if(!$ok)throw new RuntimeException($label);$n++;echo "PASS $label\n";}
function outcount301(int $id): int {return (int)one("SELECT COUNT(*) n FROM ca_outbox WHERE meter_id=? AND unique_key LIKE 'forecast_daily:%'",[$id])['n'];}
try{
    $root->exec('CREATE DATABASE `'.$dbName.'` CHARACTER SET utf8mb4');
    $config=['host'=>'127.0.0.1','port'=>$port,'database'=>$dbName,'user'=>getenv('CAMPUS_TEST_DB_USER')?:'root','password'=>getenv('CAMPUS_TEST_DB_PASSWORD')?:'','app_key'=>base64_encode(random_bytes(32)),'secure_cookie'=>false];
    file_put_contents(ROOT.'/storage/config.php',"<?php return ".var_export($config,true).";\n");
    foreach(explode(';',file_get_contents(ROOT.'/schema.sql')) as $stmt)if(trim($stmt)!=='')db()->exec($stmt);
    set_setting('auth_mutex',1);set_setting('channels_migrated_v23',true);set_setting('model_migrated_v30',true);
    $password=bin2hex(random_bytes(20));$username='tester'.bin2hex(random_bytes(4));sql("INSERT INTO ca_users(username,password_hash,role,created) VALUES (?,?,'admin',?)",[$username,password_hash($password,PASSWORD_DEFAULT),microtime(true)]);$u=one('SELECT * FROM ca_users WHERE username=?',[$username]);
    $school=bin2hex(random_bytes(5));$account=bin2hex(random_bytes(10));$room=bin2hex(random_bytes(8));$series=hash('sha256',$school.':'.$account.':'.$room.':0');$now=microtime(true);
    $body=['label'=>'离线回归测试项目','school_code'=>$school,'account'=>$account,'roomverify'=>'','enabled'=>true,'options'=>['forecast_notice_enabled'=>true,'forecast_notice_hours'=>12]];
    $id=save_meter($body,$u);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301(isset(state($id,'schedule')['next']),'new meter immediately scheduled');
    foreach([50.,42.,34.,26.,18.] as $i=>$balance){$at=$now-32*3600+$i*8*3600;perform_query($m,$at,'manual',fn()=>['balance'=>$balance,'balance_raw'=>(string)$balance,'series'=>$series,'room'=>'临时测试项目','rooms'=>[['roomverify'=>$room,'label'=>'临时测试项目']]]);}
    $count=(int)one('SELECT COUNT(*) n FROM ca_readings WHERE meter_id=?',[$id])['n'];verify301($count===5&&!json_decode(latest($m)['analysis'],true)['provisional'],'mature history accumulated through mock queries');
    $body['id']=$id;$body['label']='修改后的名称';$body['options']=['warning_kwh'=>21];save_meter($body,$u);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);
    verify301((int)$m['revision']===1&&latest($m)!==null,'label and warning changes keep active history');verify301(options($m)['forecast_notice_enabled']&&(float)options($m)['forecast_notice_hours']===12.,'omitted options retained');
    $d=dashboard($m);verify301(count($d['history'])===5&&count($d['intervals'])===4&&$d['forecast']['available'],'charts and forecast survive settings save');verify301($d['next_query']!==null&&isset(state($id,'schedule')['status']),'next query rebuilt without waiting for cron');
    $body['roomverify']=$room;save_meter($body,$u);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301((int)$m['revision']===1&&latest($m)!==null,'confirming auto-resolved same room retains revision');
    $body['options']=['min_gap_minutes'=>120,'window_end'=>'23:00'];save_meter($body,$u);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301((int)$m['revision']===1&&state($id,'schedule')['next']!==null,'window and gap edit retain observations and plan');
    $body['roomverify']=bin2hex(random_bytes(8));save_meter($body,$u);$different=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301((int)$different['revision']!==1&&latest($different)===null,'different room isolates training');$archive=dashboard($different,14,1);verify301($archive['viewing_history']&&count($archive['history'])===5&&!$archive['forecast']['available'],'previous identity remains readable in archive');
    $body['roomverify']=$room;save_meter($body,$u);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301((int)$m['revision']===1&&latest($m)!==null,'reselecting identical series restores its history');
    sql('UPDATE ca_meters SET revision=revision+100,next_query=0 WHERE id=?',[$id]);sql("DELETE FROM ca_state WHERE meter_id=? AND name='schedule'",[$id]);migrate_v301();$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301((int)$m['revision']===1&&latest($m)!==null,'upgrade repairs previously hidden matching history');verify301(state($id,'schedule')['next']!==null,'upgrade repairs missing schedule');migrate_v301();verify301((int)one('SELECT COUNT(*) n FROM ca_readings WHERE meter_id=?',[$id])['n']===$count,'upgrade idempotent and no history deleted');
    sql('INSERT INTO ca_user_channels(user_id,kind,enabled,secret,mode,templates) VALUES (?,?,?,?,?,?)',[$u['id'],'daily',1,seal('unused-offline'), 'fixed',json_value(['key1'=>'{label}周五电量通报','key2'=>'{time}','key3'=>'{summary}'])]);
    $ch=channel_for_meter($m,'daily');$wire=notification_wire($m,$ch,['event_name'=>'预测提醒','alert_level'=>'预警','summary_short'=>'测试内容'],$now);verify301(count($wire)===5&&$wire['key1']==='预测提醒'&&$wire['key2']==='预警'&&$wire['key3']===$m['label']&&$wire['key5']==='测试内容','legacy default maps to five fields without Friday title');
    $custom=channel_templates('daily',['key1'=>'自定义{event}','key2'=>'自定义{time}','key3'=>'自定义{summary}']);verify301($custom['key1']==='自定义{event}'&&$custom['key4']==='自定义{time}'&&$custom['key5']==='自定义{summary}','custom legacy title time content preserved');
    $a=json_decode(latest($m)['analysis'],true);$a['forecast_run']['origin']=$now;$a['forecast_run']['zero_center']=20.;$a['provisional']=false;$a['forecast_expired']=false;$a['rate']=1.;
    forecast_notice($m,$a,$now);verify301(outcount301($id)===0,'20 hours does not alert at 12-hour threshold');forecast_notice($m,$a,$now+8*3600);verify301(outcount301($id)===0,'exact 12 hours does not alert');forecast_notice($m,$a,$now+8*3600+1);verify301(outcount301($id)===1,'strictly below 12 hours queues daily notice');
    for($i=2;$i<12;$i++)forecast_notice($m,$a,$now+8*3600+$i);verify301(outcount301($id)===1,'minute ticks do not repeat same incident');
    $pending=one("SELECT * FROM ca_outbox WHERE meter_id=? AND unique_key LIKE 'forecast_daily:%'",[$id]);verify301($pending['channel']==='daily'&&$pending['incident']===null,'forecast uses daily channel independent of emergency incident');
    $learning=$a;$learning['provisional']=true;verify301(forecast_notice_candidate($learning,$now,options($m))===null,'learning forecast excluded');$expired=$a;$expired['forecast_run']['origin']=$now-73*3600;verify301(forecast_notice_candidate($expired,$now,options($m))===null,'expired forecast excluded');$unknown=$a;$unknown['forecast_run']['zero_center']=null;verify301(forecast_notice_candidate($unknown,$now,options($m))===null,'unknown empty time excluded');
    $off=options($m);$off['forecast_notice_enabled']=false;sql('UPDATE ca_meters SET settings=? WHERE id=?',[json_value($off),$id]);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);$calls=0;flush_notices($now+8*3600+2,function()use(&$calls){$calls++;return [200,['code'=>200]];});verify301($calls===0&&one('SELECT state FROM ca_outbox WHERE id=?',[$pending['id']])['state']==='cancelled','disabled threshold cancels queued forecast without delivery');
    $off['forecast_notice_enabled']=true;sql('UPDATE ca_meters SET settings=? WHERE id=?',[json_value($off),$id]);$m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);$high=$a;$high['forecast_run']['origin']=$now+15*3600;$high['forecast_run']['zero_center']=20.;forecast_notice($m,$high,$now+15*3600);$low=$high;$low['forecast_run']['zero_center']=11.;forecast_notice($m,$low,$now+15*3600);verify301(outcount301($id)===2,'fresh recovery rearms a later risk episode after cooldown');
    verify301(quota($id,date('Y-m-d',(int)$now))===0,'manual observations and notices do not consume automatic quota');
    $delivered=[];flush_notices($now+15*3600+1,function($channel,$wire)use(&$delivered){$delivered[]=$wire;return [200,['code'=>200]];});verify301(count($delivered)===1&&count($delivered[0])===5&&$delivered[0]['key2']==='预警','queued forecast renders five fields through mocked delivery');
    $before=outcount301($id);forecast_notice($m,$low,$now+15*3600+2);verify301(outcount301($id)===$before,'accepted notification does not repeat');
    perform_query($m,$now+60,'manual',function()use($body,$u,$series,$room){$edit=$body;$edit['options']=['critical_kwh'=>18];save_meter($edit,$u);return ['balance'=>17.,'balance_raw'=>'17','series'=>$series,'room'=>'临时测试项目','rooms'=>[['roomverify'=>$room,'label'=>'临时测试项目']]];});
    $m=one('SELECT * FROM ca_meters WHERE id=?',[$id]);verify301(json_decode(latest($m)['analysis'],true)['critical'],'in-flight query uses newly saved alarm thresholds');
    if(getenv('CAMPUS_KEEP_TEST_DB')==='1')file_put_contents(ROOT.'/.test-runtime/ui-login.json',json_value(['username'=>$username,'password'=>$password,'database'=>$dbName]));
    echo "$n integration checks passed; all queries and deliveries mocked\n";
}finally{
    if(getenv('CAMPUS_KEEP_TEST_DB')!=='1'){$root->exec('DROP DATABASE IF EXISTS `'.$dbName.'`');if(file_exists(ROOT.'/storage/config.php'))unlink(ROOT.'/storage/config.php');}
}
