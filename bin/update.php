<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
require dirname(__DIR__).'/app/monitor.php';require ROOT.'/app/dashboard.php';require ROOT.'/app/migrate.php';
if(!file_exists(ROOT.'/storage/config.php'))exit("未安装，请使用网页安装向导\n");
$lock=@fopen(ROOT.'/storage/worker.lock','c');
if($lock===false){fwrite(STDERR,"无法写入 storage/worker.lock。请检查 storage 目录及现有锁文件是否允许当前运行用户写入。\n");exit(1);}
if(!flock($lock,LOCK_EX|LOCK_NB)){fclose($lock);fwrite(STDERR,"定时任务正在运行，请暂停后重试\n");exit(1);}
$name='campus_php_'.substr(hash('sha256',realpath(ROOT).config()['database']),0,32);
try{db();if((int)one('SELECT GET_LOCK(?,0) acquired',[$name])['acquired']!==1)throw new RuntimeException('worker busy');migrate_v301();echo "3.0.1 更新完成。匹配的历史与查询计划已修复，日常通道使用五字段模板；提前通知请在项目设置中开启。\n";}catch(Throwable $e){fwrite(STDERR,"更新失败，请核对数据库权限、配置或任务状态；保留备份，不要重装。\n");exit(1);}finally{try{sql('SELECT RELEASE_LOCK(?)',[$name]);}catch(Throwable $e){}flock($lock,LOCK_UN);fclose($lock);}
