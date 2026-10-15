<?php
declare(strict_types=1);
function weighted_rate(array $rows,float $now,float $halfLife): float {$lambda=log(2)/($halfLife*3600);$sum=0.;$weights=0.;foreach($rows as $r){$w=(exp(-$lambda*max(0,$now-$r['ts']))-exp(-$lambda*max(0,$now-$r['start'])))/$lambda;if($w>0&&$r['hours']>0){$sum+=$w*$r['energy']/$r['hours'];$weights+=$w;}}return $weights>0?$sum/$weights:0.;}
function bucket(float $hours): int {return $hours<=3?0:($hours<=8?1:($hours<=24?2:3));}
function p90(array $a): float {sort($a,SORT_NUMERIC);return $a[(int)ceil(count($a)*.9)-1];}
/** Pure model: the previous forecast is supplied before learning this reading. */
function analyse_baseline(?array $previous,array $intervals,array $residuals,?array $forecast,array $reading,float $now,array $o): array {
    $old=$previous?json_decode($previous['analysis'],true):[];$balance=(float)$reading['balance'];$segment=(float)($old['segment']??0);$quality='usable';$tag='first';$speed=null;$residual=null;$plateau=null;$newInterval=null;$newResidual=null;
    $alpha=$old['anomaly_state']??[];$hits=(int)($alpha['hits']??0);$first=(float)($alpha['first']??$now);$accelerated=(bool)($alpha['confirmed']??false);$recoveryHits=(int)($alpha['recovery_hits']??0);$recoveryStart=(float)($alpha['recovery_start']??$now);$cumulative=(float)($alpha['cumulative']??0)*pow(2,-max(0,$now-($alpha['updated']??$now))/(12*3600));
    if($previous){$elapsed=($now-$previous['ts'])/3600;if($elapsed<=0)throw new AppError('采样时间未递增，请检查服务器时钟');$delta=(float)$previous['balance']-$balance;
        if($delta<-$o['reading_tolerance_kwh']){$quality='recharge_possible';$tag='increase';$segment=$now;$hits=0;$accelerated=false;$cumulative=0;$recoveryHits=0;$intervals=[];$residuals=[];}
        elseif($delta<0){$quality='small_increase';$tag='noise';}
        elseif(abs($delta)<1e-9){$plateau=$old['plateau_start']??(float)$previous['ts'];$expected=($old['rate']??0)*($now-$plateau)/3600;$quality=$now-$plateau>=10800&&$expected>.5?'stale_suspected':'unchanged';$tag='plateau';}
        else {$start=(float)($old['plateau_start']??$previous['ts']);$hours=($now-$start)/3600;$speed=$delta/$hours;$batch=($old['quality']??'')==='stale_suspected';$gap=$hours>24;$short=$hours<1/12;$quality=$batch?'batch_possible':($gap?'gap_possible':($short?'short_interval':'usable'));$tag=$batch?'batch':($gap?'gap':($short?'short_sample':'consumption'));
            if($forecast&&!$forecast['provisional']&&!$batch&&!$gap&&!$short){$expected=(float)$forecast['rate']*$hours;$residual=$delta-$expected;$samples=[];foreach($residuals as $r)if(bucket((float)$r['hours'])===bucket($hours))$samples[]=max(0,(float)$r['residual']);$margin=count($samples)>=40?p90($samples):0;$threshold=max($o['anomaly_absolute_kwh'],$expected*$o['anomaly_relative'],$margin);$cumulative=max(0,$cumulative+$residual-max(.1,.1*$expected));$suspect=$residual>$threshold||($cumulative>1.5&&$hits>=2);
                if($residual>max(.1,.1*$expected)){if($hits===0)$first=$start;$hits++;}else{$hits=0;}
                if($suspect){$recoveryHits=0;if($hits>=2&&$now-$first>=7200)$accelerated=true;elseif(!$accelerated)$tag='anomaly_candidate';}
                elseif($accelerated){if($recoveryHits===0)$recoveryStart=$now;$recoveryHits++;if($recoveryHits>=2&&$now-$recoveryStart>=14400)$accelerated=false;}
                $newResidual=['ts'=>$now,'hours'=>$hours,'residual'=>$residual];$residuals[]=$newResidual;
            }
            $learned=!$batch&&!$gap&&!$short&&($tag!=='anomaly_candidate'||$accelerated);$newInterval=['start'=>$start,'ts'=>$now,'hours'=>$hours,'energy'=>$delta,'usable'=>(int)$learned,'tag'=>$tag];if($learned)$intervals[]=$newInterval;
        }
    }
    $intervals=array_values(array_filter($intervals,fn($r)=>(int)$r['usable']===1&&$r['ts']>=max($segment,$now-28*86400)&&$r['ts']<=$now));$residuals=array_values(array_filter($residuals,fn($r)=>$r['ts']>=max($segment,$now-28*86400)&&$r['ts']<=$now));
    $fast=weighted_rate($intervals,$now,$o['fast_half_life_hours']);$slow=weighted_rate($intervals,$now,$o['slow_half_life_hours']);$rate=.7*$fast+.3*$slow;if($accelerated)$rate=max($rate,$fast);$guard=$o['guard_multiplier']*max($fast,$slow);if($speed!==null&&$quality==='usable'&&$tag==='anomaly_candidate')$guard=max($guard,$speed*$o['guard_multiplier']);$margins=[0.,0.,0.,0.];
    for($i=0;$i<4;$i++){$samples=[];foreach($residuals as $r)if(bucket((float)$r['hours'])===$i)$samples[]=max(0,(float)$r['residual']);$margins[$i]=max($i?$margins[$i-1]:0,count($samples)>=40?p90($samples):0);}
    $coverage=array_sum(array_column($intervals,'hours'));$provisional=count($intervals)<3||$coverage<24||$rate<=1e-6;$available=$balance-$o['reserve_kwh'];$center=$rate>1e-6?max(0,$available)/$rate:null;$guardHours=null;
    if($guard>1e-6){$upper=fn(float $h)=>max($guard*$h,$rate*$h+$margins[bucket($h)]);if($available<=0)$guardHours=0.;elseif($upper(72)>=$available){$lo=0.;$hi=72.;for($i=0;$i<40;$i++){$mid=($lo+$hi)/2;if($upper($mid)>=$available)$hi=$mid;else $lo=$mid;}$guardHours=$hi;}else $guardHours=72.01;}
    $recent=array_values(array_filter($intervals,fn($r)=>$now-$r['ts']<=8*3600));$evidence=count($recent)>=2&&array_sum(array_column($recent,'hours'))>=2&&$now-$recent[count($recent)-1]['ts']<=3*3600;$trusted=in_array($quality,['usable','unchanged'],true);
    $critical=(($trusted||$quality==='short_interval')&&$balance<=$o['critical_kwh'])||($trusted&&!$provisional&&$center!==null&&$center<=$o['critical_hours']&&$evidence);$warning=$balance<=$o['warning_kwh']||(!$provisional&&$guardHours!==null&&$guardHours<=$o['warning_hours']);$clear=$balance>$o['clear_kwh']&&($provisional||$guardHours===null||$guardHours>$o['clear_hours']);if($provisional&&$intervals)$clear=false;
    $a=['balance'=>$balance,'quality'=>$quality,'tag'=>$tag,'rate'=>$rate,'fast_rate'=>$fast,'slow_rate'=>$slow,'guard_rate'=>$guard,'guard_margins'=>$margins,'center_hours'=>$center,'guard_hours'=>$guardHours,'provisional'=>$provisional,'samples'=>count($intervals),'coverage_hours'=>$coverage,'speed'=>$speed,'residual'=>$residual,'accelerated'=>$accelerated,'suspect'=>$tag==='anomaly_candidate','critical'=>$critical,'warning'=>$warning,'can_clear'=>$clear,'plateau_start'=>$plateau,'segment'=>$segment,'anomaly_state'=>['hits'=>$hits,'first'=>$first,'confirmed'=>$accelerated,'cumulative'=>$cumulative,'updated'=>$now,'recovery_hits'=>$recoveryHits,'recovery_start'=>$recoveryStart]];
    return ['analysis'=>$a,'interval'=>$newInterval,'residual'=>$newResidual];
}

require_once __DIR__.'/engine.php';
