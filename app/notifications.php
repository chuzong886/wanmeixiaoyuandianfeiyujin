<?php
declare(strict_types=1);
function template_defaults(string $kind): array {return $kind==='daily'?['key1'=>'{label}周五电量通报','key2'=>'{time}','key3'=>'{summary}']:['key1'=>'{label}，剩余{balance}，请及时充值','key2'=>'{balance}'];}
function render_fields(array $templates,array $vars): array {$map=[];foreach($vars as $k=>$v)$map['{'.$k.'}']=(string)$v;$out=[];foreach($templates as $k=>$v)$out[$k]=strtr($v,$map);return $out;}
function notification_wire(array $m,array $ch,array $payload,float $now): array {
    if($ch['mode']==='mapped')return $payload;
    $r=latest($m);$vars=['label'=>$m['label'],'time'=>$payload['event_time']??date('Y-m-d H:i:s',(int)$now),'balance'=>$payload['balance_display']??($r?number_format((float)$r['balance'],1,'.','').'度':'暂无数据'),'summary'=>$payload['summary_short']??'低电量，请及时充值','voice'=>$payload['voice_text']??'请查看电量监控','event'=>$payload['event_name']??'低电量提醒'];
    return render_fields(isset($ch['templates'])?json_decode($ch['templates'],true):state((int)$m['id'],'templates:'.$ch['kind'],template_defaults($ch['kind'])),$vars);
}
function public_ip(string $host): string {
    $host=trim($host,'[]');$ips=filter_var($host,FILTER_VALIDATE_IP)?[$host]:@gethostbynamel($host);
    if(!$ips)throw new AppError('通知主机无法解析');foreach($ips as $ip)if(!filter_var($ip,FILTER_VALIDATE_IP,FILTER_FLAG_NO_PRIV_RANGE|FILTER_FLAG_NO_RES_RANGE))throw new AppError('通知目标须使用公网地址');return $ips[0];
}
function webhook_url(string $url): array {$p=parse_url($url);if(!$p||($p['scheme']??'')!=='https'||empty($p['host'])||isset($p['user'])||isset($p['pass'])||isset($p['fragment'])||(isset($p['port'])&&$p['port']!==443))throw new AppError('Webhook 须为公网 HTTPS 地址，端口443');public_ip($p['host']);return $p;}
function send_webhook(string $url,array $payload): void {
    $p=webhook_url($url);$ip=public_ip($p['host']);$ch=curl_init($url);curl_setopt_array($ch,[CURLOPT_POST=>true,CURLOPT_POSTFIELDS=>json_value($payload),CURLOPT_HTTPHEADER=>['Content-Type: application/json'],CURLOPT_RETURNTRANSFER=>true,CURLOPT_CONNECTTIMEOUT=>8,CURLOPT_TIMEOUT=>20,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS,CURLOPT_RESOLVE=>[$p['host'].':443:'.$ip],CURLOPT_WRITEFUNCTION=>static fn($c,$s)=>strlen($s)]);$ok=curl_exec($ch);$code=curl_getinfo($ch,CURLINFO_HTTP_CODE);curl_close($ch);if($ok===false||$code<200||$code>=300)throw new AppError('Webhook 未确认受理，请检查接收端');
}
function smtp_validate(array $s): void {
    if(!preg_match('/^[A-Za-z0-9.-]{1,253}$/D',$s['host']??'')||!in_array((int)($s['port']??0),[465,587],true)||!in_array($s['security']??'',['tls','starttls'],true))throw new AppError('SMTP须使用公网主机、465或587端口及TLS');
    if(($s['security']==='tls'&&(int)$s['port']!==465)||($s['security']==='starttls'&&(int)$s['port']!==587))throw new AppError('465选择TLS，587选择STARTTLS');public_ip($s['host']);foreach(['from','to'] as $k)if(!filter_var($s[$k]??'',FILTER_VALIDATE_EMAIL)||preg_match('/[\r\n]/',$s[$k]))throw new AppError('邮件地址无效');if(preg_match('/[\r\n]/',$s['username']??''))throw new AppError('SMTP用户名无效');
}
function send_smtp(array $s,string $subject,string $text): void {
    smtp_validate($s);$ip=public_ip($s['host']);$context=stream_context_create(['ssl'=>['verify_peer'=>true,'verify_peer_name'=>true,'peer_name'=>$s['host'],'SNI_enabled'=>true]]);$socket=@stream_socket_client(($s['security']==='tls'?'tls':'tcp').'://'.$ip.':'.$s['port'],$errno,$err,10,STREAM_CLIENT_CONNECT,$context);if(!$socket)throw new AppError('SMTP连接失败');stream_set_timeout($socket,15);
    $read=static function(array $codes)use($socket){$last='';$size=0;do{$line=fgets($socket,1024);if($line===false||($size+=strlen($line))>16000)throw new AppError('SMTP响应失败或超时');$last=$line;}while(isset($line[3])&&$line[3]==='-');if(!in_array((int)substr($last,0,3),$codes,true))throw new AppError('SMTP服务器拒绝请求，请检查配置');};
    $cmd=static function(string $line,array $codes)use($socket,$read){$raw=$line."\r\n";while($raw!==''){$n=fwrite($socket,$raw);if(!$n)throw new AppError('SMTP写入失败');$raw=substr($raw,$n);}$read($codes);};
    try{$read([220]);$cmd('EHLO campus-monitor.local',[250]);if($s['security']==='starttls'){$cmd('STARTTLS',[220]);if(@stream_socket_enable_crypto($socket,true,STREAM_CRYPTO_METHOD_TLS_CLIENT)!==true)throw new AppError('SMTP TLS失败');$cmd('EHLO campus-monitor.local',[250]);}
        if(($s['username']??'')!==''){$cmd('AUTH LOGIN',[334]);$cmd(base64_encode($s['username']),[334]);$cmd(base64_encode($s['password']??''),[235]);}$cmd('MAIL FROM:<'.$s['from'].'>',[250]);$cmd('RCPT TO:<'.$s['to'].'>',[250,251]);$cmd('DATA',[354]);$message='From: '.$s['from']."\r\nTo: ".$s['to']."\r\nSubject: =?UTF-8?B?".base64_encode($subject)."?=\r\nDate: ".date(DATE_RFC2822)."\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: base64\r\n\r\n".chunk_split(base64_encode($text),76,"\r\n");$cmd($message.'.',[250]);$cmd('QUIT',[221]);
    }finally{fclose($socket);}
}
function send_extra(array $ch,array $payload): void {$v=json_decode(unseal($ch['secret']),true);if($ch['kind']==='webhook')send_webhook($v['url'],$payload);else send_smtp($v,$payload['title'],json_value($payload));}
