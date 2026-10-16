<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
require dirname(__DIR__).'/app/monitor.php';require ROOT.'/app/dashboard.php';require ROOT.'/app/migrate.php';
if(!file_exists(ROOT.'/storage/config.php'))exit("未安装，请使用网页安装向导\n");
$lock=@fopen(ROOT.'/storage/worker.lock','c');
if($lock===false){fwrite(STDERR,"无法写入 storage/worker.lock。请检查 storage 目录及现有锁文件是否允许当前运行用户写入。\n");exit(1);}
if(!flock($lock,LOCK_EX|LOCK_NB)){fclose($lock);fwrite(STDERR,"定时任务正在运行，请暂停后重试\n");exit(1);}
$name='campus_php_'.substr(hash('sha256',realpath(ROOT).config()['database']),0,32);
try{db();if((int)one('SELECT GET_LOCK(?,0) acquired',[$name])['acquired']!==1)throw new RuntimeException('worker busy');migrate_v302();echo "3.0.2 更新完成。同一宿舍的匹配历史将跨配置版本继续参与预测，计划已按当前时间重建；请确认每分钟任务运行，必要时立即查询获取新读数。\n";}catch(Throwable $e){fwrite(STDERR,"更新失败，请核对数据库权限、配置或任务状态；保留备份，不要重装。\n");exit(1);}finally{try{sql('SELECT RELEASE_LOCK(?)',[$name]);}catch(Throwable $e){}flock($lock,LOCK_UN);fclose($lock);}
