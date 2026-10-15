<?php
declare(strict_types=1);
function ts_settle(array $m,array $reading,float $now): array {
    $learning=state((int)$m['id'],'ts_learning:'.$m['revision'].':'.$reading['series'],ts_learning());
    // The campus adapter currently provides no verified meter timestamp. Never manufacture labels.
    if(!isset($reading['meter_ts'])||!($reading['freshness_confirmed']??false)||!($reading['recharge_verified_absent']??false))return $learning;
    $runs=sql('SELECT f.*,r.balance_exact,r.meter_ts FROM ca_forecast_runs f JOIN ca_readings r ON r.id=f.anchor_id WHERE f.meter_id=? AND f.revision=? AND f.series=? AND f.origin>=? AND f.origin<? ORDER BY f.origin DESC',[$m['id'],$m['revision'],$reading['series'],$now-72*3600,$now]);$used=[];
    foreach($runs as $f){$h=($reading['meter_ts']-($f['meter_ts']??$f['origin']))/3600;$g=ts_group($h);if($h<1||$h>72||isset($used[$g])||$f['meter_ts']===null||one('SELECT id FROM ca_forecast_evaluations WHERE forecast_id=? AND horizon_group=?',[$f['id'],$g]))continue;
        $dirty=one("SELECT id FROM ca_readings WHERE meter_id=? AND revision=? AND series=? AND ts>? AND ts<? AND (meter_ts IS NULL OR quality NOT IN ('usable','unchanged')) LIMIT 1",[$m['id'],$m['revision'],$reading['series'],$f['origin'],$now]);
        $recharges=sql('SELECT quantity,status FROM ca_recharge_events WHERE meter_id=? AND revision=? AND series=? AND effective_ts>? AND effective_ts<=?',[$m['id'],$m['revision'],$reading['series'],$f['origin'],$now]);$censored=$dirty||$recharges||(float)$reading['meter_ts']!==$now||(float)$f['meter_ts']!==(float)$f['origin']; // initial strict labels: aligned fresh endpoints, no unobserved recharge.
        $actual=(float)$f['balance_exact']-(float)$reading['balance'];if($actual<0||$censored){sql('INSERT IGNORE INTO ca_forecast_evaluations(forecast_id,horizon_group,validated_at,actual_hours,status) VALUES (?,?,?,?,?)',[$f['id'],$g,$now,$h,'censored']);continue;}
        $run=json_decode($f['snapshot'],true);if(!($run['recharge_verified_absent']??false))continue;$before=$learning;$learning=ts_score($learning,$run,$actual,$h,$now,(string)$now.':'.$g);$used[$g]=true;sql('INSERT IGNORE INTO ca_forecast_evaluations(forecast_id,horizon_group,validated_at,actual_hours,actual_kwh,error_kwh,status) VALUES (?,?,?,?,?,?,?)',[$f['id'],$g,$now,$h,$actual,$actual-ts_mean($run,$h),'verified']);
    }
    set_state((int)$m['id'],'ts_learning:'.$m['revision'].':'.$reading['series'],$learning);return $learning;
}
function ts_save_forecast(array $m,array $r,array $a,int $readingId,float $now): void {
    $run=$a['forecast_run'];if($run['origin']===$now)sql('INSERT INTO ca_forecast_runs(meter_id,revision,series,anchor_id,origin,model_version,policy_version,snapshot) VALUES (?,?,?,?,?,?,?,?)',[$m['id'],$m['revision'],$r['series'],$readingId,$now,$run['algorithm_version'],$run['policy_version'],json_value($run)]);
    if($a['recharge_class']==='confirmed')sql('INSERT INTO ca_recharge_events(meter_id,revision,series,effective_ts,quantity,status,source) VALUES (?,?,?,?,?,?,?)',[$m['id'],$m['revision'],$r['series'],$r['confirmed_recharge_ts'],decimal_kwh($r['confirmed_recharge_kwh'])['exact'],'confirmed','trusted_adapter']);
    if($a['recharge_class']==='ambiguous')sql('INSERT INTO ca_recharge_events(meter_id,revision,series,effective_ts,status,source) VALUES (?,?,?,?,?,?)',[$m['id'],$m['revision'],$r['series'],$now,'ambiguous','balance_increase']);
}
