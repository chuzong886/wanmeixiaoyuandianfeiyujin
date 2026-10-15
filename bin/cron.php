<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
require dirname(__DIR__).'/app/monitor.php';
if(!file_exists(ROOT.'/storage/config.php')){fwrite(STDERR,"请先在网页完成安装\n");exit(1);}
$lock=@fopen(ROOT.'/storage/worker.lock','c');if($lock===false){fwrite(STDERR,"无法写入storage/worker.lock，请检查运行用户与目录权限\n");exit(1);}if(!flock($lock,LOCK_EX|LOCK_NB)){fclose($lock);echo "已有任务运行，本轮跳过\n";exit(0);}
$name='campus_php_'.substr(hash('sha256',realpath(ROOT).config()['database']),0,32);
try{
    if((int)one('SELECT GET_LOCK(?,0) acquired',[$name])['acquired']!==1){echo "已有数据库任务运行，本轮跳过\n";exit(0);}
    $start=microtime(true);set_setting('worker_heartbeat',$start);
    sql("UPDATE ca_attempts SET state='failed',error='进程中断，结果未知，不自动重放' WHERE state='started' AND ts<?",[$start-120]);
    sql("UPDATE ca_jobs SET state='failed',message='进程中断，请查看查询记录' WHERE state='running' AND created<?",[$start-600]);
    sql("UPDATE ca_outbox SET state='unknown',details='发送过程被中断，不自动重发' WHERE state='sending'");
    $meters=sql('SELECT m.* FROM ca_meters m JOIN ca_users u ON u.id=m.user_id WHERE m.enabled=1 AND u.enabled=1 ORDER BY m.last_tick,m.id');$processed=0;
    foreach($meters as $m){if(microtime(true)-$start>=40)break;try{tick_meter($m,microtime(true));$processed++;}catch(Throwable $e){event((int)$m['id'],microtime(true),'worker_error',['error'=>'本项目处理失败，请检查配置']);}}
    flush_notices(microtime(true),null,4);set_setting('worker_heartbeat',microtime(true));sql('SELECT RELEASE_LOCK(?)',[$name]);echo date('Y-m-d H:i:s')." 本轮检查 {$processed} 个项目\n";
}catch(Throwable $e){fwrite(STDERR,"任务失败，请检查数据库、PHP 扩展和私有配置\n");exit(1);}finally{flock($lock,LOCK_UN);fclose($lock);}
