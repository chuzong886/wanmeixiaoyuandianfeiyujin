<?php
if(PHP_SAPI!=='cli')exit;
require dirname(__DIR__).'/app/core.php';
$name=$argv[1]??'';if(!valid_username($name))exit("用法：php bin/reset-password.php 用户名\n");$u=one('SELECT id FROM ca_users WHERE username=?',[$name]);if(!$u)exit("用户不存在\n");$hide=PHP_OS_FAMILY!=='Windows'&&function_exists('system');echo "输入新密码（至少12位）：";if($hide)system('stty -echo');try{$p=rtrim((string)fgets(STDIN),"\r\n");}finally{if($hide)system('stty echo');echo "\n";}if(strlen($p)<12||strlen($p)>200)exit("密码长度无效\n");sql('UPDATE ca_users SET password_hash=?,session_version=session_version+1 WHERE id=?',[password_hash($p,PASSWORD_DEFAULT),$u['id']]);echo "密码已更新，旧会话已失效\n";
