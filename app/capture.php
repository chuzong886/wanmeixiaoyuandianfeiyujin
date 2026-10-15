<?php
declare(strict_types=1);
/** Pure, offline request parser. Never fetches a URL, executes curl or stores a capture. */
function capture_object(string $json): array {
    try{$v=json_decode($json,true,48,JSON_THROW_ON_ERROR);}catch(Throwable $e){throw new AppError('内容不是有效JSON，请粘贴完整请求或表单正文');}
    if(!is_array($v))throw new AppError('JSON必须是请求对象');return $v;
}
function capture_string(mixed $value,string $name): string {
    if(!is_string($value)&&!is_int($value))throw new AppError($name.'必须是字符串，避免编号精度丢失');return (string)$value;
}
function capture_percent(string $value): string {
    if(preg_match('/%(?![0-9a-fA-F]{2})/',$value))throw new AppError('表单含有不完整的百分号编码');return rawurldecode($value);
}
function capture_form(string $text): array {
    $out=[];foreach(explode('&',trim($text)) as $part){if($part==='')continue;$pair=explode('=',$part,2);if(count($pair)!==2)throw new AppError('表单格式错误，需要字段名=值');$key=urldecode(trim($pair[0]));if(!in_array($key,['encryptData','timestamp','randomStr','method'],true))continue;if(isset($out[$key]))throw new AppError('表单字段重复，请一次粘贴一条请求');
        // Only percent-decode ciphertext: tolerate raw Base64 '+' without converting it to a space.
        $out[$key]=$key==='encryptData'?trim($pair[1]):urldecode(trim($pair[1]));}
    if(!$out)throw new AppError('未找到encryptData、timestamp、randomStr字段');return $out;
}
function capture_decode(array $input): array {
    if(isset($input['encryptData'])){$stamp=capture_string($input['timestamp']??null,'timestamp');$random=capture_string($input['randomStr']??null,'randomStr');$key=campus_key($stamp,$random);$encrypted=capture_percent(trim(capture_string($input['encryptData'],'encryptData')));if(strlen($encrypted)>180000)throw new AppError('密文过长');$bytes=base64_decode($encrypted,true);if($bytes===false||strlen($bytes)===0||strlen($bytes)%16!==0)throw new AppError('encryptData不是完整的AES Base64密文');$plain=openssl_decrypt($bytes,'aes-128-ecb',$key,OPENSSL_RAW_DATA);if($plain===false)throw new AppError('解密失败，请确认密文、时间戳和随机串来自同一条请求');$outer=capture_object($plain);
        if(isset($input['method'])&&capture_string($input['method'],'method')!==($outer['method']??''))throw new AppError('表单method与密文内容不一致');
    }else{$outer=$input;$stamp=null;}
    $param=$outer['param']??null;if(is_string($param))$param=capture_object($param);if(!is_array($param))throw new AppError('请求缺少param，不能从响应或余额快照推断查询账户');
    $method=capture_string($outer['method']??$param['cmd']??null,'method');if(!in_array($method,['getbindroom','h5_getstuindexpage'],true)||($param['cmd']??$method)!==$method)throw new AppError('仅支持绑定宿舍及宿舍详情请求，cmd与method须一致');
    if($stamp!==null&&isset($param['timestamp'])&&capture_string($param['timestamp'],'内部timestamp')!==$stamp)throw new AppError('密文内部时间戳与表单不一致');
    $school=capture_string($outer['customercode']??null,'学校编码');$account=capture_string($param['account']??null,'查询账户');$command=capture_string($outer['command']??null,'服务标识');$room=isset($param['roomverify'])?capture_string($param['roomverify'],'宿舍标识'):'';
    if(!preg_match('/^[A-Za-z0-9_-]{1,40}$/D',$school)||!preg_match('/^[A-Za-z0-9_-]{1,80}$/D',$account)||!preg_match('/^[A-Za-z0-9_]{1,64}$/D',$command)||mb_strlen($room)>150||preg_match('/[\x00-\x1f\x7f]/',$room))throw new AppError('解出的查询字段格式无效，请检查抓包完整性');
    $label=$param['roomfullname']??$outer['roomfullname']??'';if(!is_string($label)||mb_strlen($label)>100||preg_match('/[\x00-\x1f\x7f]/',$label))$label='';
    return ['school_code'=>$school,'account'=>$account,'command'=>$command,'roomverify'=>$room,'label'=>$label,'method'=>$method,'source'=>isset($input['encryptData'])?'encrypted':'plaintext','room_missing'=>$room===''];
}
function parse_capture(string $source,array $fields=[]): array {
    if(strlen($source)>262144)throw new AppError('抓包内容最多256KB，请只复制相关请求');
    if($fields){$items=[$fields];}else{$text=trim($source);if($text==='')throw new AppError('请粘贴请求，或填写三个加密字段');
        if($text[0]==='{'||$text[0]==='['){$json=capture_object($text);if(isset($json['log']['entries'])){$items=[];$entries=$json['log']['entries'];if(!is_array($entries))throw new AppError('HAR结构无效');foreach($entries as $entry){if(!is_array($entry)||!is_array($entry['request']??null))continue;$request=$entry['request'];if(!is_string($request['url']??null))continue;$url=parse_url($request['url']??'');if(($url['host']??'')!=='xqh5.17wanxiao.com'||($url['path']??'')!=='/smartWaterAndElectricityService/SWAEEncryptServlet'||($request['method']??'')!=='POST')continue;$post=$request['postData']??[];if(!is_array($post))throw new AppError('HAR请求正文格式无效');
                if(!empty($post['text']))$items[]=capture_form((string)$post['text']);elseif(isset($post['params'])){if(!is_array($post['params']))throw new AppError('HAR字段列表无效');$f=[];foreach($post['params'] as $p){if(!is_array($p))throw new AppError('HAR字段格式无效');$k=$p['name']??'';if(!in_array($k,['encryptData','timestamp','randomStr','method'],true))continue;if(isset($f[$k]))throw new AppError('HAR表单含重复字段');$f[$k]=$p['value']??'';}$items[]=$f;}if(count($items)>20)throw new AppError('HAR相关请求最多20条，请缩小导出范围');}
            if(!$items)throw new AppError('HAR中没有找到校园水电POST请求');
        }elseif(array_is_list($json)){$items=$json;if(count($items)>20)throw new AppError('一次最多解析20条请求');}else{$items=[$json];}}
        elseif(preg_match('/(?:^|\s)curl(?:\.exe)?\s/i',$text)){$args=[];preg_match_all('/(?:--data(?:-raw|-binary|-urlencode)?|-d)(?:\s+|=)(?:\'([^\']*)\'|"((?:\\\\.|[^"\\\\])*)")/s',$text,$matches,PREG_SET_ORDER);foreach($matches as $match)$args[]=($match[1]??'')!==''?$match[1]:stripcslashes($match[2]??'');if(!$args)throw new AppError('未识别curl的带引号表单正文；可改为粘贴表单或分别输入三个字段');$items=[capture_form(implode('&',$args))];}
        else{if(preg_match('/\r?\n\r?\n/',$text)){$parts=preg_split('/\r?\n\r?\n/',$text,2);$text=$parts[1];}$items=[capture_form($text)];}
    }
    $candidates=[];$seen=[];foreach($items as $item){if(!is_array($item))throw new AppError('请求列表中存在无效对象');$r=capture_decode($item);$key=json_value([$r['school_code'],$r['account'],$r['command'],$r['roomverify']]);if(!isset($seen[$key])){$seen[$key]=true;$candidates[]=$r;}}
    if(!$candidates)throw new AppError('没有可解析的请求');return ['candidates'=>$candidates,'notes'=>['学校编码不是学院编号；不能仅凭编号确定学校名称。','绑定请求可能不含宿舍标识，请改用详情请求或保存后手动查询绑定房间。','请求中通常没有宿舍名称、当前电量或今日用量；本功能不会联网查询，也不会保存原始抓包。']];
}
