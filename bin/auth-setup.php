<?php
declare(strict_types=1);
if(PHP_SAPI!=='cli'){http_response_code(404);exit;}
require dirname(__DIR__).'/app/core.php';require ROOT.'/app/auth.php';
// Recovery bootstrap when an already-active ESA rule blocks an older unconfigured login client.
if(!file_exists(ROOT.'/storage/config.php'))exit("请先完成安装\n");
$path=realpath($argv[1]??'');$storage=realpath(ROOT.'/storage');
if(!$path||!$storage||!str_starts_with($path,$storage.DIRECTORY_SEPARATOR)||strtolower(pathinfo($path,PATHINFO_EXTENSION))!=='json')exit("仅接受storage目录中的JSON认证配置文件\n");
try{$body=json_decode(file_get_contents($path),true,16,JSON_THROW_ON_ERROR);if(!is_array($body))throw new RuntimeException();save_auth_options($body);echo "认证配置已保存；请用正式域名访问/login验证。请删除这份临时JSON凭据文件。\n";}catch(Throwable $e){fwrite(STDERR,"配置失败，请检查JSON格式、身份标、场景和32～128位回源密钥；未输出任何凭据。\n");exit(1);}
