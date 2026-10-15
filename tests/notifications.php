<?php
require dirname(__DIR__).'/app/core.php';require ROOT.'/app/notifications.php';require ROOT.'/app/model.php';
$checks=0;function check(bool $v,string $s): void{global $checks;if(!$v)throw new RuntimeException($s);$checks++;echo "PASS $s\n";}
$vars=['label'=>'测试项目','time'=>'2026-10-12 22:00:00','balance'=>'2.5度','summary'=>'请充值','voice'=>'测试项目剩余2.5度，请及时充值','event'=>'低电量'];
$daily=render_fields(template_defaults('daily'),$vars);check($daily===['key1'=>'测试项目周五电量通报','key2'=>'2026-10-12 22:00:00','key3'=>'请充值'],'daily three fixed fields');$em=render_fields(template_defaults('emergency'),$vars);check($em['key1']==='测试项目，剩余2.5度，请及时充值'&&$em['key2']==='2.5度','shared emergency key contains dorm balance and voice reminder');check(render_fields(['key1'=>'{voice}','key2'=>'{event} {balance}'],$vars)===['key1'=>'测试项目剩余2.5度，请及时充值','key2'=>'低电量 2.5度'],'custom voice and event template');check(render_fields(['key1'=>'literal'],$vars)['key1']==='literal','literal custom text preserved');
foreach(['127.0.0.1','10.0.0.1','192.168.1.2','169.254.169.254','::1'] as $ip){try{public_ip($ip);check(false,'private rejected');}catch(AppError $e){check(true,'private notification address rejected');}}
check(public_ip('8.8.8.8')==='8.8.8.8','public address allowed without network request');
foreach([['host'=>'8.8.8.8','port'=>25,'security'=>'none'],['host'=>'8.8.8.8','port'=>465,'security'=>'starttls'],['host'=>'8.8.8.8','port'=>465,'security'=>'tls','from'=>"a@example.com\r\nBcc: b@example.com",'to'=>'c@example.com']] as $s){try{smtp_validate($s);check(false,'invalid SMTP rejected');}catch(AppError $e){check(true,'invalid SMTP configuration rejected');}}
$o=default_options();$previous=['ts'=>100000.,'balance'=>100.,'analysis'=>json_value([])];$r=analyse_reading($previous,[],[],null,['balance'=>99.],100001.,$o);check($r['analysis']['quality']==='short_interval'&&!$r['interval']['usable'],'rapid samples cannot generate trusted velocity spike');
$low=analyse_reading($previous,[],[],null,['balance'=>2.],100001.,$o);check($low['analysis']['critical'],'rapid sample of actual low balance still alerts');
echo "$checks notification and rapid-sampling checks passed; no messages sent\n";
