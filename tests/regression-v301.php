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
    // Reproduce the missed case: the current revision already has a cold-start row.
    $otherRoom=bin2hex(random_bytes(8));$otherSeries=hash('sha256',$school.':'.$account.':'.$otherRoom.':0');
    $secondBody=['label'=>'跨版本连续训练测试','school_code'=>$school,'account'=>$account,'roomverify'=>$otherRoom,'enabled'=>true,'options'=>['window_start'=>'00:00','window_end'=>'23:59']];
    $second=save_meter($secondBody,$u);$mm=one('SELECT * FROM ca_meters WHERE id=?',[$second]);
    foreach([50.,42.,34.,26.,18.] as $i=>$balance){$at2=$now-48*3600+$i*8*3600;perform_query($mm,$at2,'manual',fn()=>['balance'=>$balance,'balance_raw'=>(string)$balance,'series'=>$otherSeries,'room'=>'临时连续训练项目','rooms'=>[['roomverify'=>$otherRoom,'label'=>'临时连续训练项目']]]);}
    sql('UPDATE ca_meters SET revision=2 WHERE id=?',[$second]);$mm=one('SELECT * FROM ca_meters WHERE id=?',[$second]);$coldAt=$now-3600;$cold=analyse_reading(null,[],[],null,['balance'=>10.,'balance_raw'=>'10','series'=>$otherSeries],$coldAt,options($mm))['analysis'];
    $coldAttempt=reserve_attempt($mm,$coldAt,'manual');sql('INSERT INTO ca_readings(meter_id,attempt_id,revision,series,ts,balance,balance_raw,balance_exact,room,quality,analysis) VALUES (?,?,?,?,?,?,?,?,?,?,?)',[$second,$coldAttempt,2,$otherSeries,$coldAt,10.,'10','10.000000','临时连续训练项目',$cold['quality'],json_value($cold)]);sql("UPDATE ca_attempts SET state='ok' WHERE id=?",[$coldAttempt]);
    verify301($cold['provisional'],'fixture reproduces current revision with a cold-start reading');
    $combined=dashboard($mm);verify301(count($combined['history'])===6&&count($combined['intervals'])===4&&$combined['reused_history_readings']===5,'current monitor includes matching observations and intervals across revisions');
    $viewOld=dashboard($mm,14,1);verify301(count($viewOld['history'])===5&&$viewOld['viewing_history']&&$viewOld['manual_reason']==='','archive allows verifying current dorm to continue monitoring');
    $beforeSource=one('SELECT snapshot FROM ca_forecast_runs WHERE meter_id=? ORDER BY id LIMIT 1',[$second])['snapshot'];
    set_state($second,'schedule',['day'=>date('Y-m-d',(int)$now-86400),'next'=>$now-86400]);sql('UPDATE ca_meters SET next_query=? WHERE id=?',[$now-86400,$second]);
    $preview=dashboard($mm);verify301($preview['plan_pending_worker']&&$preview['schedule']['day']===date('Y-m-d',(int)$now),'dashboard previews a current plan when stored plan belongs to yesterday');verify301(state($second,'schedule')['day']!==$preview['schedule']['day'],'viewing a preview never silently writes or runs a query');
    perform_query($mm,$now-1800,'manual',fn()=>['balance'=>10.,'balance_raw'=>'10','series'=>$otherSeries,'room'=>'临时连续训练项目','rooms'=>[['roomverify'=>$otherRoom,'label'=>'临时连续训练项目']]]);verify301(json_decode(latest($mm)['analysis'],true)['provisional'],'cached cold forecast is not relabeled mature or fresh merely by finding older data');
    perform_query($mm,$now,'manual',fn()=>['balance'=>9.,'balance_raw'=>'9','series'=>$otherSeries,'room'=>'临时连续训练项目','rooms'=>[['roomverify'=>$otherRoom,'label'=>'临时连续训练项目']]]);
    $updated=json_decode(latest($mm)['analysis'],true);verify301(!$updated['provisional']&&$updated['coverage_hours']>=32&&$updated['samples']>=4,'fresh query trains using old matching intervals despite nonempty current revision');
    verify301((int)latest($mm)['revision']===2,'new reading stays in current revision without rewriting history');
    verify301(one('SELECT snapshot FROM ca_forecast_runs WHERE meter_id=? ORDER BY id LIMIT 1',[$second])['snapshot']===$beforeSource,'old prediction snapshot never rewritten as a new forecast');
    $plan=state($second,'schedule');verify301($plan['day']===date('Y-m-d',(int)$now)&&$plan['basis_reading_id']===(int)latest($mm)['id']&&$plan['next']>=$now,'manual query replaces yesterday plan with current basis and time');
    verify301(!schedule_is_stale($mm,$now),'saved plan signature matches current configuration and reading');
    $oldNext=$plan['next'];rebuild_schedule($mm,$now+60);verify301(state($second,'schedule')['next']<=$oldNext+60,'refresh does not repeatedly postpone an earlier plan');
    $tickAt=(float)strtotime(date('Y-m-d',(int)$now).' +1 day 12:00');$queries=0;tick_meter($mm,$tickAt,function()use(&$queries,$otherSeries,$otherRoom){$queries++;return ['balance'=>8.,'balance_raw'=>'8','series'=>$otherSeries,'room'=>'临时连续训练项目','rooms'=>[['roomverify'=>$otherRoom,'label'=>'临时连续训练项目']]];});
    verify301($queries===1&&(float)latest($mm)['ts']===$tickAt,'cron repairs day rollover and actually executes a fresh automatic query');
    verify301(!json_decode(latest($mm)['analysis'],true)['provisional'],'automatic continuation keeps historical load learning');
    verify301(state($second,'schedule')['next']>=$tickAt&&(float)state($second,'schedule')['calculated_at']===$tickAt,'automatic task writes latest plan instead of old timestamps');
    $ownOther=dashboard($m);verify301($ownOther['reused_history_readings']===0,'another dorm never receives this matching-series history');
    $differentBinding=$secondBody;$differentBinding['id']=$second;$differentBinding['roomverify']=bin2hex(random_bytes(8));save_meter($differentBinding,$u);$unmatched=one('SELECT * FROM ca_meters WHERE id=?',[$second]);verify301(latest($unmatched)===null&&dashboard($unmatched)['reused_history_readings']===0,'same meter changing to a different dorm never reuses unrelated observations');
    set_setting('history_continuity_v302',false);migrate_v302();verify301(setting('app_version')==='3.0.2','3.0.2 upgrade completes');$recordsBefore=(int)one('SELECT COUNT(*) n FROM ca_readings')['n'];migrate_v302();verify301((int)one('SELECT COUNT(*) n FROM ca_readings')['n']===$recordsBefore,'continuity upgrade is idempotent and preserves rows');
    if(getenv('CAMPUS_KEEP_TEST_DB')==='1')file_put_contents(ROOT.'/.test-runtime/ui-login.json',json_value(['username'=>$username,'password'=>$password,'database'=>$dbName]));
    echo "$n integration checks passed; all queries and deliveries mocked\n";
}finally{
    if(getenv('CAMPUS_KEEP_TEST_DB')!=='1'){$root->exec('DROP DATABASE IF EXISTS `'.$dbName.'`');if(file_exists(ROOT.'/storage/config.php'))unlink(ROOT.'/storage/config.php');}
}
