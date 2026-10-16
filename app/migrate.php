<?php
declare(strict_types=1);
function migrate_v23(): void {
    foreach(explode(';',(string)file_get_contents(ROOT.'/schema.sql')) as $s)if(trim($s)!=='')db()->exec($s);
    if(!one("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ca_users' AND COLUMN_NAME='phone'"))db()->exec('ALTER TABLE ca_users ADD phone VARCHAR(11) NULL,ADD UNIQUE KEY phone_idx(phone)');
    if(!one("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ca_outbox' AND COLUMN_NAME='channel_version'"))db()->exec('ALTER TABLE ca_outbox ADD channel_version INT NOT NULL DEFAULT 0');
    transaction(function(){
        if(setting('channels_migrated_v23',false))return;
        foreach(sql('SELECT id FROM ca_users ORDER BY id') as $u){$notes=[];foreach(['daily','emergency','webhook','smtp'] as $kind){
            if(account_channel((int)$u['id'],$kind))continue;
            $rows=sql("SELECT c.*,m.label FROM ca_channels c JOIN ca_meters m ON m.id=c.meter_id WHERE m.user_id=? AND c.kind=? AND c.secret<>'' ORDER BY c.meter_id",[$u['id'],$kind]);if(!$rows)continue;
            $first=$rows[0];$plain=unseal($first['secret']);$tpl=state((int)$first['meter_id'],'templates:'.$kind,template_defaults($kind));$conflict=false;
            foreach($rows as $r)if(unseal($r['secret'])!==$plain||(int)$r['enabled']!==(int)$first['enabled']||$r['mode']!==$first['mode']||json_value(state((int)$r['meter_id'],'templates:'.$kind,template_defaults($kind)))!==json_value($tpl))$conflict=true;
            sql('INSERT INTO ca_user_channels(user_id,kind,enabled,secret,mode,templates) VALUES (?,?,?,?,?,?)',[$u['id'],$kind,$conflict?0:(int)$first['enabled'],$first['secret'],$first['mode'],json_value($tpl)]);
            if($conflict)$notes[]=$kind.'原宿舍配置不同，已暂存“'.$first['label'].'”配置并关闭。请在账户通知页面确认后启用；旧配置可在迁移记录中查看。';
        }if($notes)set_setting('channel_migration:'.$u['id'],$notes);}
        sql("UPDATE ca_outbox SET state='cancelled',details='2.3账户通道迁移，请等待新事件' WHERE state='queued'");set_setting('channels_migrated_v23',true);
    });set_setting('app_version','2.3.0');
}

function migrate_v30(): void {
    migrate_v23();
    foreach(['balance_raw'=>'VARCHAR(80) NULL','balance_exact'=>'DECIMAL(18,6) NULL','meter_ts'=>'DOUBLE NULL'] as $name=>$type)if(!one("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ca_readings' AND COLUMN_NAME=?",[$name]))db()->exec('ALTER TABLE ca_readings ADD `'.$name.'` '.$type);
    if(!one("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ca_intervals' AND COLUMN_NAME='energy_exact'"))db()->exec('ALTER TABLE ca_intervals ADD energy_exact DECIMAL(18,6) NULL');
    foreach(['energy_lower','energy_upper'] as $col)if(!one("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ca_intervals' AND COLUMN_NAME=?",[$col]))db()->exec('ALTER TABLE ca_intervals ADD `'.$col.'` DECIMAL(18,6) NULL');
    // Old DOUBLE values remain unchanged; raw source digits and meter time cannot be reconstructed.
    transaction(function(){if(!setting('model_migrated_v30',false)){sql("UPDATE ca_outbox SET state='cancelled',details='3.0预测策略更新，请等待新事件' WHERE state='queued'");sql("DELETE FROM ca_state WHERE name='schedule'");sql('UPDATE ca_meters SET next_query=0');set_setting('model_migrated_v30',true);}set_setting('app_version','3.0.0');});
}

function migrate_v301(): void {
    migrate_v30();
    if(setting('model_migrated_v301',false)){set_setting('app_version','3.0.1');return;}
    foreach(sql('SELECT id FROM ca_meters ORDER BY id') as $item)transaction(function()use($item){
        $m=one('SELECT * FROM ca_meters WHERE id=? FOR UPDATE',[$item['id']]);
        if(!latest($m)){$room=$m['roomverify'];if($room===''){$rooms=state((int)$m['id'],'rooms',[]);if(count($rooms)===1)$room=(string)$rooms[0]['roomverify'];}
            if($room!==''){$series=hash('sha256',$m['school_code'].':'.unseal($m['account_secret']).':'.$room.':'.$m['business_type']);$prior=one('SELECT revision FROM ca_readings WHERE meter_id=? AND series=? ORDER BY ts DESC,id DESC LIMIT 1',[$m['id'],$series]);if($prior){$m['revision']=(int)$prior['revision'];sql('UPDATE ca_meters SET revision=? WHERE id=?',[$m['revision'],$m['id']]);set_state((int)$m['id'],'risk',['level'=>'normal']);event((int)$m['id'],microtime(true),'history_restored',['revision'=>$m['revision']]);}}
        }
        rebuild_schedule($m,microtime(true));
    });
    set_setting('model_migrated_v301',true);set_setting('app_version','3.0.1');
}
