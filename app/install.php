<?php
declare(strict_types=1);
function environment_checks(): array { $result=['PHP >= 8.2'=>version_compare(PHP_VERSION,'8.2','>='),'64 位 PHP'=>PHP_INT_SIZE>=8];foreach(['pdo_mysql','curl','openssl','session','mbstring'] as $ext)$result['扩展 '.$ext]=extension_loaded($ext);$result['storage 可写']=is_dir(ROOT.'/storage')&&is_writable(ROOT.'/storage');return $result; }
function setup_token(): void {if(file_exists(ROOT.'/storage/config.php'))return;if(!is_writable(ROOT.'/storage'))return;$path=ROOT.'/storage/setup.token';$f=@fopen($path,'x');if($f){fwrite($f,bin2hex(random_bytes(24)));fclose($f);chmod($path,0600);}}
function install_app(array $body): void {
    if(file_exists(ROOT.'/storage/config.php'))throw new AppError('安装已完成，入口已锁定',409);
    $checks=environment_checks();if(in_array(false,$checks,true))throw new AppError('环境检查未通过，请先安装扩展并设置目录权限');
    $lock=fopen(ROOT.'/storage/install.lock','c');if(!$lock||!flock($lock,LOCK_EX|LOCK_NB))throw new AppError('另一个安装任务正在进行',409);
    try{
        if(file_exists(ROOT.'/storage/config.php'))throw new AppError('安装已完成',409);$token=trim((string)@file_get_contents(ROOT.'/storage/setup.token'));if($token===''||!hash_equals($token,trim((string)($body['setup_token']??''))))throw new AppError('安装码错误，请在宝塔查看 storage/setup.token',403);
        $host=trim((string)($body['host']??''));$port=(int)($body['port']??3306);$name=trim((string)($body['database']??''));$dbUser=trim((string)($body['db_user']??''));$dbPass=(string)($body['db_password']??'');$username=trim((string)($body['username']??''));$password=(string)($body['password']??'');
        if(!preg_match('/^[A-Za-z0-9.-]{1,253}$/D',$host)||!preg_match('/^[A-Za-z0-9_]{1,64}$/D',$name)||$port<1||$port>65535||$dbUser===''||$dbPass==='')throw new AppError('数据库信息不完整或格式错误');if(!valid_username($username)||strlen($password)<12||strlen($password)>200)throw new AppError('管理员用户名须为3～32位字母数字，密码为12～200字节');
        $https=($_SERVER['HTTPS']??'')==='on';if(!$https&&!in_array($_SERVER['SERVER_NAME']??'',['127.0.0.1','localhost'],true))throw new AppError('请先为正式网站开启 HTTPS，再安装');
        $c=['host'=>$host,'port'=>$port,'database'=>$name,'user'=>$dbUser,'password'=>$dbPass,'app_key'=>base64_encode(random_bytes(32)),'secure_cookie'=>$https];$d=connect_db($c);
        foreach(explode(';',(string)file_get_contents(ROOT.'/schema.sql')) as $statement)if(trim($statement)!=='')$d->exec($statement);
        if((int)$d->query('SELECT COUNT(*) FROM ca_users')->fetchColumn()>0)throw new AppError('该数据库已有本系统账户，请恢复旧配置文件或使用空数据库，不能重新接管',409);
        $d->beginTransaction();try{
            $q=$d->prepare("INSERT INTO ca_users(username,password_hash,role,created) VALUES (?,?,'admin',?)");$q->execute([$username,password_hash($password,PASSWORD_DEFAULT),microtime(true)]);
            foreach(['registration_enabled'=>(bool)($body['registration_enabled']??true),'max_meters_per_user'=>5,'site_title'=>'校园电量','auth_mutex'=>1,'worker_heartbeat'=>0,'app_version'=>'3.0.2','history_continuity_v302'=>true,'model_migrated_v301'=>true,'model_migrated_v30'=>true,'channels_migrated_v23'=>true] as $key=>$v){$q=$d->prepare('INSERT INTO ca_settings(`name`,value) VALUES (?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)');$q->execute([$key,json_value($v)]);}
            $tmp=ROOT.'/storage/config.tmp.'.bin2hex(random_bytes(6));$text="<?php\nreturn ".var_export($c,true).";\n";if(file_put_contents($tmp,$text,LOCK_EX)===false)throw new AppError('配置写入失败');chmod($tmp,0640);if(!rename($tmp,ROOT.'/storage/config.php')){unlink($tmp);throw new AppError('配置文件保存失败');}$d->commit();
        }catch(Throwable $e){if($d->inTransaction())$d->rollBack();throw $e;}
        @unlink(ROOT.'/storage/setup.token');file_put_contents(ROOT.'/storage/installed.lock',date(DATE_ATOM));chmod(ROOT.'/storage/installed.lock',0640);
    }finally{flock($lock,LOCK_UN);fclose($lock);}
}
