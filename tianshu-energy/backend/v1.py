"""Versioned business rules for the v1 console; migrated in place without deleting history."""
import base64,collections,datetime as dt,hashlib,hmac,http.client,json,math,secrets,smtplib,ssl,statistics,threading,urllib.parse,uuid
from email.message import EmailMessage
from energy import Error,clock,local,number,text,jdump,tariff_default,atomic

TYPES={'readonly_meter':('meter',False),'control_meter':('meter',True),'socket':('appliance',True),'breaker':('circuit',True),'appliance':('appliance',False)}

class V1Mixin:
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs)
  with self.lock:
   for table,fields in {'devices':{'device_type':"TEXT NOT NULL DEFAULT ''",'meter_parent_id':"TEXT NOT NULL DEFAULT ''",'controller_id':"TEXT NOT NULL DEFAULT ''",'created_at':'REAL NOT NULL DEFAULT 0','scale':'REAL NOT NULL DEFAULT 1'},'alerts':{'machine_state':"TEXT NOT NULL DEFAULT 'active'",'handling_state':"TEXT NOT NULL DEFAULT 'pending'",'assigned_to':"TEXT NOT NULL DEFAULT ''",'occurrences':'INTEGER NOT NULL DEFAULT 1','recovered_at':'REAL'},'schedules':{'weekdays':"TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6'",'last_result':"TEXT NOT NULL DEFAULT ''"}}.items():
    existing={r['name'] for r in self.rows('PRAGMA table_info('+table+')')}
    for key,decl in fields.items():
     if key not in existing:self.db.execute('ALTER TABLE '+table+' ADD COLUMN '+key+' '+decl)
   self.db.executescript('''
CREATE TABLE IF NOT EXISTS scopes(device_id TEXT NOT NULL,effective REAL NOT NULL,counted INTEGER NOT NULL,parent_id TEXT NOT NULL,PRIMARY KEY(device_id,effective));
CREATE TABLE IF NOT EXISTS tariff_versions(space_id TEXT NOT NULL,effective REAL NOT NULL,tariff TEXT NOT NULL,PRIMARY KEY(space_id,effective));
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,space_id TEXT NOT NULL,name TEXT NOT NULL,config TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS rule_state(id TEXT PRIMARY KEY,since REAL,normal_since REAL,last_notice REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS incident_notes(id INTEGER PRIMARY KEY AUTOINCREMENT,alert_id TEXT NOT NULL,actor TEXT NOT NULL,ts REAL NOT NULL,note TEXT NOT NULL,photo TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,config BLOB NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,alert_id TEXT,created REAL NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL,error TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recharges(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,ts REAL NOT NULL,amount REAL NOT NULL,kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS space_members(space_id TEXT NOT NULL,user_id TEXT NOT NULL,PRIMARY KEY(space_id,user_id));
''')
   for d in self.rows('SELECT * FROM devices'):
    dtype=d['device_type'] or ('readonly_meter' if d['kind']=='meter' or d['source']=='third_party' else 'breaker' if d['kind']=='circuit' else 'socket')
    self.db.execute('UPDATE devices SET device_type=?,controllable=? WHERE id=?',(dtype,int(TYPES[dtype][1] and not d['protected'] and d['source']!='third_party'),d['id']))
    if not self.one('SELECT 1 FROM scopes WHERE device_id=?',(d['id'],)):self.db.execute('INSERT INTO scopes VALUES(?,?,?,?)',(d['id'],0,d['counted'],d['meter_parent_id']))
   for s in self.spaces():self.db.execute('INSERT OR IGNORE INTO tariff_versions VALUES(?,?,?)',(s['id'],0,jdump(s['tariff'])))
   self.db.execute("UPDATE alerts SET machine_state='normal',handling_state=CASE WHEN handling_state='pending' THEN 'pending' ELSE handling_state END WHERE state='resolved'")
   self.db.commit()
  self.delivery_busy=False;self.reset_tokens={}
  from ai_analysis import AIAnalysis
  self.ai=AIAnalysis(self)

 @atomic
 def save_space(self,user,b):
  s=super().save_space(user,b)
  if self.one("SELECT name FROM sqlite_master WHERE name='tariff_versions'"):self.db.execute('INSERT OR IGNORE INTO tariff_versions VALUES(?,?,?)',(s['id'],0,jdump(s['tariff'])));self.db.commit()
  return s

 @atomic
 def save_schedule(self,user,b):
  weekdays=b.get('weekdays',list(range(7)))
  if not isinstance(weekdays,list) or not weekdays or any(type(x) is not int or x not in range(7) for x in weekdays):raise Error('请选择有效的执行日期')
  existing=self.rows('SELECT * FROM schedules WHERE device_id=? AND at=? AND enabled=1',(b.get('device_id'),b.get('at')))
  if any(set(str(x) for x in weekdays)&set(r['weekdays'].split(',')) for r in existing):raise Error('该设备在所选时间已存在计划',409)
  result=super().save_schedule(user,b);self.db.execute('UPDATE schedules SET weekdays=? WHERE id=?',(','.join(str(x) for x in sorted(set(weekdays))),result['id']));self.db.commit();return result

 def access(self,user,sid,write=False):
  if user.get('role') in ('admin','demo'):return
  if not self.one('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(sid,user.get('id',''))):raise Error('当前账户没有该空间的权限',403)
  if write:self.require(user,('operator',))

 @atomic
 def login(self,body):
  row=self.one('SELECT role FROM users WHERE username=?',(str(body.get('username','')),))
  if row and row['role']=='disabled':raise Error('账户已停用',403)
  return super().login(body)

 def capabilities(self,d):
  dtype=d.get('device_type') or 'readonly_meter';source=d['source'];mapping={}
  if source=='third_party':
   try:c=self.api.config(d['id']);mapping={'balance_kwh':True} if c['adapter']=='wanxiao' else {k:bool(v) for k,v in c['mapping'].items()}
   except (Error,ValueError):pass
  return {'power':source!='third_party' and dtype!='appliance' or bool(mapping.get('power_w')),'energy':source!='third_party' and dtype!='appliance' or bool(mapping.get('balance_kwh') or mapping.get('cumulative_kwh') or mapping.get('power_w')),'balance':bool(mapping.get('balance_kwh')),'voltage':source!='third_party' and dtype!='appliance' or bool(mapping.get('voltage')),'switch':TYPES.get(dtype,('',False))[1] and source!='third_party','per_phase':False}

 @atomic
 def devices(self,sid):
  result=super().devices(sid)
  for d in result:
   caps=self.capabilities(d);d['capabilities']=caps;d['controllable']=caps['switch'] and not d['protected']
   d['communication']='online' if d['online'] else 'unknown' if not d['last_sample'] else 'offline'
   if not caps['switch']:d['relay_on']=None
   if d.get('device_type')=='appliance':d['online']=False;d['communication']='unknown';d['quality']='missing'
   d['switch_state']='unknown' if not caps['switch'] or not d['online'] else 'closed' if d['relay_on'] else 'open'
   if not caps['power']:d['has_power']=False;d['power_w']=None;d['current_a']=None
   if not caps['voltage']:d['has_voltage']=False;d['voltage']=None
   d['quality']='measured' if d['online'] else 'missing' if not d['last_sample'] else 'expired'
   if d['source']=='simulated':d['quality']='demo'
   d['updated_at']=d['last_sample'] or None
   command=self.one('SELECT state,created,finished,detail FROM commands WHERE device_id=? ORDER BY created DESC LIMIT 1',(d['id'],));d['last_command']=command
   d['execution']='none' if not command else 'running' if command['state'] in ('queued','dispatched') else 'success' if command['state']=='confirmed' else 'failed' if command['state']=='rejected' else 'unknown'
  return result

 def descendants(self,did):
  found=[];pending=[did];seen={did}
  while pending:
   parent=pending.pop()
   for row in self.rows('SELECT * FROM devices WHERE enabled=1 AND (controller_id=? OR meter_parent_id=?)',(parent,parent)):
    if row['id'] not in seen:seen.add(row['id']);pending.append(row['id']);found.append(row)
  return found

 @atomic
 def command(self,user,b,from_schedule=False):
  d=self.device(text(b.get('device_id')))
  if not from_schedule:self.access(user,d['space_id'],True)
  if not self.capabilities(d)['switch']:raise Error('此设备没有经过配置的开关能力',409)
  impacted=self.descendants(d['id'])
  if not b.get('desired') and any(x['protected'] for x in impacted):raise Error('控制范围内包含保护设备，禁止断电',409)
  if from_schedule:
   hold=self.one('SELECT value FROM settings WHERE key=?',('manual:'+d['id'],))
   if hold and float(hold['value'])>clock():raise Error('手动操作优先，自动计划已跳过',409)
  result=super().command(user,b,from_schedule)
  if not from_schedule:
   with self.lock:self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",('manual:'+d['id'],str(clock()+300)));self.db.commit()
  return result

 def save_device(self,user,b):
  with self.lock:
   b=dict(b);dtype=b.get('device_type') or ('readonly_meter' if b.get('kind')=='meter' or b.get('source')=='third_party' else 'breaker' if b.get('kind','circuit')=='circuit' else 'socket')
   if dtype not in TYPES:raise Error('设备类型无效')
   old=self.device(b['id']) if b.get('id') else None
   if old and dtype!=old['device_type']:raise Error('已有设备类型不可直接转换，请建立替换设备',409)
   sid=text(b.get('space_id'));parent=b.get('meter_parent_id') or '';controller=b.get('controller_id') or '';scale=number(b.get('scale',1),.000001,1000000)
   for value in (parent,controller):
    if value:
     linked=self.device(value)
     if linked['space_id']!=sid or value==b.get('id'):raise Error('关联设备必须属于当前空间且不能关联自身')
     if b.get('id') and any(x['id']==value for x in self.descendants(b['id'])):raise Error('关联关系会形成循环')
   if controller and not self.capabilities(self.device(controller))['switch']:raise Error('关联控制器不具备开关能力')
   if parent and not self.capabilities(self.device(parent))['energy']:raise Error('上级计量点不具备计量能力')
   effective=number(b.get('effective_at',clock()),old['created_at'] if old else 0,clock()+366*86400)
   if old and effective<clock()-2:raise Error('统计范围变更不得追溯修改历史')
   b['kind']=TYPES[dtype][0];b['controllable']=TYPES[dtype][1];b.setdefault('nominal_w',1);b.setdefault('threshold_w',1500)
   b['counted']=dtype!='appliance' and bool(b.get('counted',True))
   d=super().save_device(user,b)
   self.db.execute('UPDATE devices SET device_type=?,meter_parent_id=?,controller_id=?,scale=?,created_at=? WHERE id=?',(dtype,parent,controller,scale,old['created_at'] if old else clock(),d['id']))
   scope_changed=not old or old['meter_parent_id']!=parent or old['counted']!=int(b['counted'])
   if scope_changed:
    self.db.execute('INSERT OR REPLACE INTO scopes VALUES(?,?,?,?)',(d['id'],effective,int(b['counted']),parent))
   self.db.commit();self.report_cache.clear();return self.device(d['id'])

 def _selected(self,ts):
  result={}
  for d in self.rows('SELECT * FROM devices WHERE created_at<=?',(ts,)):
   r=self.one('SELECT * FROM scopes WHERE device_id=? AND effective<=? ORDER BY effective DESC LIMIT 1',(d['id'],ts))
   if r and r['counted']:result[d['id']]=r
  selected=set()
  for did,r in result.items():
   parent=r['parent_id'];seen={did};covered=False
   while parent and parent not in seen:
    seen.add(parent)
    if parent in result:covered=True;break
    p=self.one('SELECT parent_id FROM scopes WHERE device_id=? AND effective<=? ORDER BY effective DESC LIMIT 1',(parent,ts));parent=p['parent_id'] if p else ''
   if not covered:selected.add(did)
  return selected

 def _energy_rows(self,sid,start,end):
  raw=self.rows('SELECT h.*,d.created_at FROM hourly h JOIN devices d ON d.id=h.device_id WHERE d.space_id=? AND h.hour>=? AND h.hour<?',(sid,start,end));buckets=collections.defaultdict(dict)
  for r in raw:buckets[r['hour']][r['device_id']]=r
  out=[]
  for hour,values in sorted(buckets.items()):
   boundaries=[float(hour),float(hour+3600)]+[r['effective'] for r in self.rows('SELECT DISTINCT effective FROM scopes WHERE effective>? AND effective<?',(hour,hour+3600))]
   boundaries=sorted(set(boundaries));kwh=coverage=0
   for left,right in zip(boundaries,boundaries[1:]):
    expected=self._selected((left+right)/2)&{d['id'] for d in self.rows('SELECT id FROM devices WHERE space_id=?',(sid,))};fraction=(right-left)/3600
    kwh+=sum(values[did]['energy_wh']/1000*fraction for did in expected if did in values)
    if expected:coverage+=min((min(3600,values[did]['coverage_s']) if did in values else 0) for did in expected)*fraction
   out.append({'hour':hour,'kwh':kwh,'coverage_s':coverage})
  return out

 @atomic
 def snapshot(self,sid):
  result=super().snapshot(sid);devices=result['devices'];selected=self._selected(clock())&{d['id'] for d in devices}
  for d in devices:d['counted']=d['id'] in selected
  known=[d for d in devices if d['counted'] and d['online'] and d['has_power']]
  result['power_kw']=sum(d['power_w'] for d in known)/1000 if known else None;result['power_available']=bool(known);result['power_complete']=bool(selected) and all(d['online'] and d['has_power'] for d in devices if d['counted']);result['collector_healthy']=bool(self.last_tick and clock()-self.last_tick<5 and not self.last_error)
  result['pending_incidents']=self.one("SELECT COUNT(*) n FROM alerts WHERE space_id=? AND handling_state!='closed'",(sid,))['n']
  return result

 def space(self,sid):
  s=super().space(sid)
  if self.one("SELECT name FROM sqlite_master WHERE name='tariff_versions'"):
   version=self.one('SELECT tariff FROM tariff_versions WHERE space_id=? AND effective<=? ORDER BY effective DESC LIMIT 1',(sid,clock()))
   if version:s['tariff']=json.loads(version['tariff'])
  return s

 def price_at(self,sid,ts):
  r=self.one('SELECT tariff FROM tariff_versions WHERE space_id=? AND effective<=? ORDER BY effective DESC LIMIT 1',(sid,ts));return json.loads(r['tariff']) if r else self.space(sid)['tariff']

 def _cost(self,sid,rows):
  cumulative=0;costs={};billing_month=None
  for r in rows:
   current_month=local(r['hour']).strftime('%Y-%m')
   if billing_month!=current_month:cumulative=0;billing_month=current_month
   t=self.price_at(sid,r['hour']);amount=r['kwh'];cost=amount*t['prices'][local(r['hour']).hour]
   if t.get('mode')=='tiered':
    cost=0;remaining=amount;tiers=t.get('tiers',[])
    for band in tiers:
     limit=float(band.get('limit',1e12));take=min(remaining,max(0,limit-cumulative));cost+=take*float(band['price']);cumulative+=take;remaining-=take
    if remaining>0 and tiers:cost+=remaining*float(tiers[-1]['price']);cumulative+=remaining
   else:cumulative+=amount
   costs[r['hour']]=cost
  return costs

 @atomic
 def reports(self,sid,days=7):
  r=super().reports(sid,days);r=json.loads(jdump(r));now=clock();start=local(now).replace(hour=0,minute=0,second=0,microsecond=0)-dt.timedelta(days=r['days']-1);month=start.replace(day=1);rows=self._energy_rows(sid,month.timestamp(),now+3600);costs=self._cost(sid,rows)
  for day in r['daily']:
   subset=[x for x in rows if local(x['hour']).date().isoformat()==day['date']];day['cost']=sum(costs[x['hour']] for x in subset) if subset else None
   if not day['covered_hours']:day['kwh']=None;day['cost']=None
  if not rows or not any(x['coverage_s'] for x in rows):r['peak_hours']=[];r['peak_share']=None
  r['estimated_shift_saving']=sum(x['kwh']*.1*max(0,self.price_at(sid,x['hour'])['prices'][local(x['hour']).hour]-min(self.price_at(sid,x['hour'])['prices'])) for x in rows if x['hour']>=start.timestamp() and local(x['hour']).hour in r['peak_hours'])
  rank=collections.defaultdict(float)
  for x in self.rows('SELECT h.*,d.name,d.area FROM hourly h JOIN devices d ON d.id=h.device_id WHERE d.space_id=? AND h.hour>=? AND h.hour<=?',(sid,start.timestamp(),now)):
   if x['device_id'] in self._selected(x['hour']+3599):rank[x['device_id']]+=x['energy_wh']/1000
  r['ranking']=[{**{k:self.one('SELECT * FROM devices WHERE id=?',(did,))[k] for k in ('id','name','area','source')},'kwh':amount} for did,amount in sorted(rank.items(),key=lambda x:-x[1])];return r

 @atomic
 def totals(self,sid,now):
  r=super().totals(sid,now);day=local(now).replace(hour=0,minute=0,second=0,microsecond=0);month=day.replace(day=1);rows=self._energy_rows(sid,month.timestamp(),now+3600);costs=self._cost(sid,rows)
  original=r['month_cost'];r['month_cost']=sum(costs.values());r['today_cost']=sum(cost for hour,cost in costs.items() if hour>=day.timestamp());r['forecast_cost']+=r['month_cost']-original;r['forecast_low']=max(r['month_cost'],r['forecast_cost']*.8);r['forecast_high']=r['forecast_cost']*1.2;r['budget_percent']=r['month_cost']/r['budget']*100 if r['budget'] else None;r['remaining_budget']=r['budget']-r['month_cost'] if r['budget'] else None
  if not rows:r['today_kwh']=None;r['month_kwh']=None;r['today_cost']=None;r['month_cost']=None
  return r

 @atomic
 def save_tariff(self,user,b):
  self.require(user,('admin','demo'));b=dict(b);t=b.get('tariff',{});mode=t.get('mode','time_of_use')
  if mode not in ('fixed','time_of_use','tiered'):raise Error('计费模式无效')
  if mode=='fixed':t['prices']=[number(t.get('fixed_price',.72),0,100)]*24;t['bands']=['平']*24
  if mode=='tiered':
   tiers=t.get('tiers',[])
   if not tiers:raise Error('请添加阶梯价格')
   previous=0
   for tier in tiers:
    limit=number(tier.get('limit'),previous+.000001,1e12);number(tier.get('price'),0,100);previous=limit
  effective=number(b.get('effective_at',clock()),0,clock()+366*86400)
  if effective<clock()-2:raise Error('新电价不能追溯改变历史')
  result=super().save_tariff(user,b);self.db.execute('INSERT OR REPLACE INTO tariff_versions VALUES(?,?,?)',(b['space_id'],effective,jdump(result)));self.db.commit();self.report_cache.clear();return result

 def alert(self,key,sid,did,level,title,message,condition,now):
  existing=self.one("SELECT * FROM alerts WHERE key=? AND state IN ('active','acknowledged')",(key,))
  if condition is None:
   if existing:self.db.execute("UPDATE alerts SET machine_state='unknown',updated=? WHERE id=?",(now,existing['id']))
   return
  if condition and not existing:
   recovered=self.one("SELECT * FROM alerts WHERE key=? AND handling_state!='closed' ORDER BY created DESC LIMIT 1",(key,))
   if recovered:
    self.db.execute("UPDATE alerts SET state='active',machine_state='active',occurrences=occurrences+1 WHERE id=?",(recovered['id'],));existing=recovered;self.enqueue_notice(recovered)
  super().alert(key,sid,did,level,title,message,condition,now)
  current=self.one('SELECT * FROM alerts WHERE key=? ORDER BY created DESC LIMIT 1',(key,))
  if current:self.db.execute('UPDATE alerts SET machine_state=?,recovered_at=? WHERE id=?',('active' if condition else 'normal',None if condition else now,current['id']))
  if condition and current:
   if not existing:self.enqueue_notice(current)
   self.db.execute('UPDATE alerts SET level=? WHERE id=?',('critical' if existing and existing['level']=='critical' else level,current['id']))

 def evaluate(self,key,sid,did,level,title,condition,now,duration=0,recovery=10):
  state=self.one('SELECT * FROM rule_state WHERE id=?',(key,)) or {'since':None,'normal_since':None}
  if condition is None:self.alert(key,sid,did,level,title,'',None,now);return
  since=(state['since'] if state['since'] is not None else now) if condition else None;normal=(state['normal_since'] if state['normal_since'] is not None else now) if not condition else None
  self.db.execute('INSERT INTO rule_state(id,since,normal_since) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET since=excluded.since,normal_since=excluded.normal_since',(key,since,normal))
  if condition and now-since>=duration:self.alert(key,sid,did,level,title,title,True,now)
  elif not condition and now-normal>=recovery:self.alert(key,sid,did,level,title,title,False,now)

 def check_alerts(self,now):
  custom_power={json.loads(r['config']).get('device_id') for r in self.rows('SELECT config FROM rules WHERE enabled=1') if json.loads(r['config']).get('metric')=='power'}
  for s in self.spaces():
   for d in self.devices(s['id']):
    fresh=d['online'];caps=d['capabilities'];power=d['power_w']
    if caps['power'] and d['id'] not in custom_power:self.evaluate('overload:'+d['id'],s['id'],d['id'],'warning','功率超过阈值',power>=d['threshold_w'] if fresh and d['has_power'] else None,now,recovery=10)
    if caps['voltage']:self.evaluate('voltage:'+d['id'],s['id'],d['id'],'critical','电压异常',not d['nominal_voltage']*.9<=d['voltage']<=d['nominal_voltage']*1.1 if fresh and d['has_voltage'] else None,now,recovery=10)
    if d['source']=='gateway':self.evaluate('offline:'+d['id'],s['id'],d['id'],'warning','设备离线',not fresh,now,duration=10)
    if d['source']=='third_party':
     if d.get('api',{}).get('configuration_error'):
      self.evaluate('api-offline:'+d['id'],s['id'],d['id'],'critical','接口配置不可读取',True,now);continue
     c=self.api.config(d['id']) if not d.get('api',{}).get('configuration_error') else {};reading=d.get('api',{}).get('reading') or {};balance=reading.get('balance');outside=False
     if c.get('adapter')=='wanxiao':
      hour=local(now).strftime('%H:%M');start=c['window_start'];end=c['window_end'];outside=not (start<=hour<=end if start<=end else hour>=start or hour<=end)
     self.evaluate('api-offline:'+d['id'],s['id'],d['id'],'warning','接口异常',not fresh if not outside else None,now,duration=30)
     if balance is not None:
      for key,level,threshold in [('api-low:','warning',c.get('warning_kwh',20)),('api-critical:','critical',c.get('critical_kwh',3))]:self.evaluate(key+d['id'],s['id'],d['id'],level,'剩余电量紧急' if level=='critical' else '剩余电量不足',balance<=threshold if fresh else None,now)
      f=d.get('api',{}).get('forecast',{}).get('forecast',{});self.evaluate('api-forecast:'+d['id'],s['id'],d['id'],'warning','可用电量即将不足',f.get('remaining_hours',1e9)<=c.get('forecast_notice_hours',12) if fresh and f.get('available') else None,now)
   t=self.totals(s['id'],now);spent=t['month_cost'];condition=None if spent is None and t['budget'] else bool(t['budget'] and (spent>=t['budget'] or t['forecast_available'] and t['forecast_cost']>=t['budget']));self.evaluate('budget:'+s['id'],s['id'],None,'warning','月度费用超出预算',condition,now)
  for r in self.rows('SELECT * FROM rules WHERE enabled=1'):
   c=json.loads(r['config']);d=next((x for x in self.devices(r['space_id']) if x['id']==c.get('device_id')),None);metric=c.get('metric');value=None
   t=self.totals(r['space_id'],now)
   if metric in ('daily_energy','daily_cost','monthly_cost'):value=t[{'daily_energy':'today_kwh','daily_cost':'today_cost','monthly_cost':'month_cost'}[metric]]
   if d and d['online']:
    if metric=='power' and d['has_power']:value=d['power_w']
    if metric=='balance':value=(d.get('api',{}).get('reading') or {}).get('balance')
    if metric=='remaining_hours':value=d.get('api',{}).get('forecast',{}).get('forecast',{}).get('remaining_hours')
    if metric=='offline':value=0
    if metric=='off_hours' and d['has_power']:
     hour=local(now).strftime('%H:%M');start=c.get('start','08:00');end=c.get('end','18:00');inside=start<=hour<=end if start<=end else hour>=start or hour<=end;value=0 if inside else d['power_w']
    if metric=='stalled':
     q=self.one('SELECT MIN(ts) ts,MIN(balance) low,MAX(balance) high FROM api_readings WHERE device_id=? AND ts>=? AND balance IS NOT NULL',(d['id'],now-float(c.get('threshold',3600))*2));value=now-q['ts'] if q and q['ts'] and q['low']==q['high'] else 0
   elif d and metric=='offline':value=1
   threshold=float(c.get('threshold',1));active=self.one("SELECT 1 FROM alerts WHERE key=? AND state IN ('active','acknowledged')",('rule:'+r['id'],));h=float(c.get('hysteresis',0)) if active else 0
   condition=None if value is None else value<=threshold+h if metric in ('balance','remaining_hours') else value>=threshold-h
   self.evaluate('rule:'+r['id'],r['space_id'],c.get('device_id'),c.get('level','warning'),r['name'],condition,now,float(c.get('duration',0)),float(c.get('recovery',10)))
   incident=self.one("SELECT * FROM alerts WHERE key=? AND machine_state='active' AND handling_state!='closed' ORDER BY created DESC LIMIT 1",('rule:'+r['id'],))
   if incident:
    state=self.one('SELECT * FROM rule_state WHERE id=?',('rule:'+r['id'],));escalate=float(c.get('escalate_after',0))
    if escalate and now-incident['created']>=escalate and incident['level']!='critical':self.db.execute("UPDATE alerts SET level='critical' WHERE id=?",(incident['id'],));incident['level']='critical';self.enqueue_notice(incident)
    if state and now-max(state['last_notice'],incident['created'])>=max(60,float(c.get('cooldown',3600))):self.enqueue_notice(incident);self.db.execute('UPDATE rule_state SET last_notice=? WHERE id=?',(now,'rule:'+r['id']))

 def enqueue_notice(self,alert):
  for ch in self.rows('SELECT * FROM channels WHERE enabled=1'):
   try:
    from protected import transform
    c=json.loads(transform(ch['config'],False))
    if c.get('space_id') and c['space_id']!=alert['space_id']:continue
    if c.get('level') and c['level']!=alert['level']:continue
    now=clock();hour=local(now).strftime('%H:%M')
    if alert['level']!='critical' and not c.get('start','00:00')<=hour<=c.get('end','23:59'):continue
    self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,ch['id'],alert['id'],now,'queued',0,now,'',jdump({'event_id':alert['id'],'title':alert['title'],'message':alert['message'],'level':alert['level'],'space_id':alert['space_id']})))
   except (Error,ValueError):continue

 def notification_tick(self):
  if self.delivery_busy:return
  with self.lock:due=self.rows("SELECT * FROM deliveries WHERE state IN ('queued','retry') AND next_try<=? ORDER BY created LIMIT 5",(clock(),))
  if not due:return
  self.delivery_busy=True
  def work():
   try:
    for row in due:
     try:
      with self.lock:ch=self.one('SELECT * FROM channels WHERE id=? AND enabled=1',(row['channel_id'],))
      if not ch:raise Error('通知渠道已停用')
      self.send_channel(ch,json.loads(row['payload']))
      with self.lock:self.db.execute("UPDATE deliveries SET state='sent',attempts=attempts+1,error='' WHERE id=?",(row['id'],));self.db.commit()
     except Exception as exc:
      attempts=row['attempts']+1
      safe=str(exc) if isinstance(exc,Error) else '发送失败，请检查渠道连接和凭据'
      with self.lock:self.db.execute('UPDATE deliveries SET state=?,attempts=?,next_try=?,error=? WHERE id=?',('failed' if attempts>=3 else 'retry',attempts,clock()+60*2**attempts,safe,row['id']));self.db.commit()
   finally:self.delivery_busy=False
  threading.Thread(target=work,daemon=True).start()

 def send_channel(self,ch,payload):
  from protected import transform
  c=json.loads(transform(ch['config'],False))
  if ch['kind'] in ('webhook','feishu'):
   u=urllib.parse.urlsplit(c['url']);connection=(http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection)(u.hostname,u.port,timeout=10)
   try:
    body=self.webhook_body(ch['kind'],c,payload);connection.request('POST',(u.path or '/')+('?' +u.query if u.query else ''),jdump(body).encode(),{'Content-Type':'application/json','X-Tianshu-Event':payload['event_id']});response=connection.getresponse();raw=response.read(4096)
    if not 200<=response.status<300:raise Error('Webhook 返回 HTTP '+str(response.status))
    if ch['kind']=='feishu':
     try:result=json.loads(raw)
     except (ValueError,UnicodeError):raise Error('飞书返回内容不是有效 JSON')
     code=result.get('code',result.get('StatusCode'))
     if code not in (0,'0'):raise Error('飞书拒绝消息：'+str(result.get('msg') or result.get('StatusMessage') or code))
   finally:connection.close()
  else:
   message=EmailMessage();message['Subject']='天枢 · '+payload['title'];message['From']=c['sender'];message['To']=c['recipients'];message.set_content(payload['message'])
   client=smtplib.SMTP_SSL(c['host'],int(c.get('port',465)),timeout=10,context=ssl.create_default_context()) if c.get('ssl',True) else smtplib.SMTP(c['host'],int(c.get('port',587)),timeout=10)
   with client:
    if not c.get('ssl',True):client.starttls(context=ssl.create_default_context())
    if c.get('username'):client.login(c['username'],c.get('password',''))
    client.send_message(message)

 def webhook_body(self,kind,c,payload,now=None):
  if kind!='feishu':return payload
  level={'critical':'紧急','warning':'预警','info':'通知'}.get(payload.get('level'),'通知');body={'msg_type':'text','content':{'text':f"【天枢{level}】{payload['title']}\n{payload['message']}\n事件 ID：{payload['event_id']}"}}
  secret=str(c.get('secret','')).strip()
  if secret:
   timestamp=str(int(clock() if now is None else now));key=(timestamp+'\n'+secret).encode();body.update(timestamp=timestamp,sign=base64.b64encode(hmac.new(key,digestmod=hashlib.sha256).digest()).decode())
  return body

 @atomic
 def tick(self,now=None):
  signature=tuple(sorted(self._selected(clock())))
  if signature!=getattr(self,'scope_signature',None):self.report_cache.clear();self.scope_signature=signature
  super().tick(now);self.notification_tick()

 def prediction(self,sid,demo=False):
  report=self.reports(sid,30);daily=[r for r in report['daily'] if r['kwh'] is not None and r['covered_hours']>=23.5];source='demo' if any(d['source']=='simulated' for d in self.devices(sid)) else 'measured';now=local();values=[x['kwh'] for x in daily]
  if demo:values=[18,21,19,24,23,16,15]*4;source='demo'
  if len(values)<3:return {'available':False,'source':source,'days':[],'suggestions':[],'samples':len(values)}
  last=values[-7:];weights=list(range(1,len(last)+1));mean=sum(v*w for v,w in zip(last,weights))/sum(weights);variation=statistics.pstdev(last);days=[]
  for i in range(1,8):
   day=now.date()+dt.timedelta(days=i);season=values[-7+(i-1)%7] if len(values)>=7 else mean;expected=max(0,mean*.6+season*.4);days.append({'date':day.isoformat(),'kwh':round(expected,2),'low':round(max(0,expected-variation),2),'high':round(expected+variation,2)})
  suggestions=[{'title':'未来七日用量','value':round(sum(x['kwh'] for x in days),2),'unit':'kWh'},{'title':'优先检查高贡献设备','device_ids':[x['id'] for x in report['ranking'][:3]]}]
  return {'available':True,'source':source,'samples':len(values),'algorithm':'天枢本地加权基线 v1','days':days,'suggestions':suggestions,'generated_at':clock()}

 @atomic
 def dispatch(self,method,path,user,b,params):
  sid=params.get('space_id') or b.get('space_id') or 'space-demo'
  with self.lock:
   if path=='/api/spaces' and method=='GET':return {'spaces':[s for s in self.spaces() if user['role'] in ('admin','demo') or self.one('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(s['id'],user.get('id','')))]}
   if path in ('/api/snapshot','/api/reports','/api/cameras','/api/commands','/api/alerts','/api/schedules','/api/prediction','/api/rules','/api/tariff-versions'):self.access(user,sid)
   if method=='GET':
    if path=='/api/ai-settings':return {'settings':self.ai.settings(user)}
    if path=='/api/ai-status':return self.ai.status(user,sid)
    if path=='/api/ai-history':return self.ai.history(user,sid)
    if path=='/api/brand':
     row=self.one("SELECT value FROM settings WHERE key='backend'");return {'settings':{'logo':json.loads(row['value']).get('logo') if row else None}}
    if path=='/api/space-assignees':
     self.access(user,sid);return {'members':self.rows("SELECT id,username FROM users u WHERE role IN ('admin','operator') AND (role='admin' OR EXISTS(SELECT 1 FROM space_members m WHERE m.user_id=u.id AND m.space_id=?))",(sid,))}
    if path=='/api/alerts':
     where=['a.space_id=?'];args=[sid]
     for key,col,valid in [('handling','a.handling_state',('pending','acknowledged','in_progress','closed')),('level','a.level',('warning','critical'))]:
      if params.get(key):
       if params[key] not in valid:raise Error('筛选值无效')
       where.append(col+'=?');args.append(params[key])
     for key,op in [('from','>='),('to','<=')]:
      if params.get(key):where.append('a.created'+op+'?');args.append(number(params[key],0,1e12))
     page=int(number(params.get('page',1),1,1000000));size=int(number(params.get('size',20),1,100));clause=' AND '.join(where);total=self.one('SELECT COUNT(*) n FROM alerts a WHERE '+clause,args)['n'];rows=self.rows('SELECT a.*,d.name device_name FROM alerts a LEFT JOIN devices d ON d.id=a.device_id WHERE '+clause+' ORDER BY a.created DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]);counts={x['machine_state']:x['n'] for x in self.rows('SELECT machine_state,COUNT(*) n FROM alerts WHERE space_id=? GROUP BY machine_state',(sid,))};counts.update({x['handling_state']:x['n'] for x in self.rows('SELECT handling_state,COUNT(*) n FROM alerts WHERE space_id=? GROUP BY handling_state',(sid,))});return {'alerts':rows,'total':total,'counts':counts}
    if path=='/api/prediction':return self.prediction(sid,params.get('demo')=='1')
    if path=='/api/impact':
     d=self.device(text(params.get('device_id')));self.access(user,d['space_id']);return {'devices':[{k:d[k] for k in ('id','name','protected','device_type')} for d in self.descendants(d['id'])]}
    if path=='/api/rules':return {'rules':[{**r,'config':json.loads(r['config'])} for r in self.rows('SELECT * FROM rules WHERE space_id=?',(sid,))]}
    if path=='/api/tariff-versions':return {'versions':[{**r,'tariff':json.loads(r['tariff'])} for r in self.rows('SELECT * FROM tariff_versions WHERE space_id=? ORDER BY effective DESC',(sid,))]}
    if path=='/api/incident':
     a=self.one('SELECT * FROM alerts WHERE id=?',(params.get('id'),))
     if not a:raise Error('事件不存在',404)
     self.access(user,a['space_id']);return {'alert':a,'notes':self.rows('SELECT * FROM incident_notes WHERE alert_id=? ORDER BY ts',(a['id'],)),'samples':self.rows('SELECT ts,power_w FROM samples WHERE device_id=? AND ts BETWEEN ? AND ? ORDER BY ts',(a['device_id'],a['created']-300,a['created']+300))}
    if path=='/api/channel-config':
     self.require(user,('admin',));row=self.one('SELECT * FROM channels WHERE id=?',(params.get('id'),))
     if not row:raise Error('渠道不存在',404)
     from protected import transform
     c=json.loads(transform(row['config'],False));c.pop('password',None);c.pop('secret',None);return {'channel':{**{k:row[k] for k in ('id','name','kind','enabled')},'config':c}}
    if path in ('/api/channels','/api/deliveries','/api/backend-settings'):
     self.require(user,('admin',))
     if path=='/api/channels':return {'channels':self.rows('SELECT id,name,kind,enabled FROM channels')}
     if path=='/api/deliveries':return {'deliveries':self.rows('SELECT id,channel_id,alert_id,created,state,attempts,error FROM deliveries ORDER BY created DESC LIMIT 300')}
     row=self.one("SELECT value FROM settings WHERE key='backend'");return {'settings':json.loads(row['value']) if row else {'port':0,'logo':None}}
   if method=='POST':
    if path=='/api/ai-settings':return self.ai.save(user,b)
    if path=='/api/ai-preview':return self.ai.prepare(user,b)
    if path=='/api/rules':
     self.require(user,('admin','demo'));self.access(user,sid);c=b.get('config',{});metric=c.get('metric')
     if metric not in ('power','daily_energy','daily_cost','monthly_cost','balance','remaining_hours','offline','off_hours','stalled'):raise Error('预警指标无效')
     if metric in ('power','balance','remaining_hours','offline','off_hours','stalled'):
      if not c.get('device_id'):raise Error('请选择规则设备')
      device=self.device(c['device_id']);caps=self.capabilities(device)
      required={'power':'power','balance':'balance','remaining_hours':'balance','off_hours':'power','stalled':'balance'}.get(metric)
      if required and not caps[required]:raise Error('该设备不支持所选预警指标')
     for key in ('threshold','duration','recovery','hysteresis','cooldown'):c[key]=number(c.get(key,0),0,1e12)
     if c.get('device_id') and self.device(c['device_id'])['space_id']!=sid:raise Error('规则设备不属于当前空间')
     rid=b.get('id') or uuid.uuid4().hex;self.db.execute('INSERT INTO rules VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,config=excluded.config,enabled=excluded.enabled',(rid,sid,text(b.get('name')),jdump(c),int(b.get('enabled',True))));self.db.commit();return {'id':rid}
    if path=='/api/incident':
     self.require(user);a=self.one('SELECT * FROM alerts WHERE id=?',(b.get('id'),))
     if not a:raise Error('事件不存在',404)
     self.access(user,a['space_id'],True);handling=b.get('handling_state',a['handling_state'])
     if handling not in ('pending','acknowledged','in_progress','closed'):raise Error('处理状态无效')
     if handling=='closed' and a['machine_state']!='normal':raise Error('异常尚未恢复，不能关闭事件',409)
     note=str(b.get('note','')).strip();photo=str(b.get('photo',''))
     if len(note)>2000 or '\x00' in note:raise Error('处理说明格式无效')
     if photo and (not photo.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')) or len(photo)>180000):raise Error('照片格式或大小无效')
     assignee=b.get('assigned_to',a['assigned_to'])
     if assignee and not self.one("SELECT id FROM users u WHERE id=? AND role IN ('admin','operator') AND (role='admin' OR EXISTS(SELECT 1 FROM space_members m WHERE m.user_id=u.id AND m.space_id=?))",(assignee,a['space_id'])):raise Error('负责人没有该空间的处理权限')
     self.db.execute('UPDATE alerts SET handling_state=?,assigned_to=? WHERE id=?',(handling,assignee,a['id']))
     if note or photo:self.db.execute('INSERT INTO incident_notes(alert_id,actor,ts,note,photo) VALUES(?,?,?,?,?)',(a['id'],user['username'],clock(),note,photo))
     self.audit(user['username'],'incident_update',a['id'],handling);self.db.commit();return {'ok':True}
    if path=='/api/automation-stop':
     self.require(user,('admin','demo'));self.db.execute('UPDATE schedules SET enabled=0 WHERE space_id=?',(sid,));self.audit(user['username'],'automation_stop',sid,'暂停当前空间全部自动计划');self.db.commit();return {'ok':True}
    if path=='/api/device-retire':
     self.require(user,('admin','demo'));d=self.device(text(b.get('id')))
     if self.descendants(d['id']):raise Error('请先迁移关联设备')
     if self.one("SELECT id FROM commands WHERE device_id=? AND state IN ('queued','dispatched')",(d['id'],)):raise Error('请等待控制指令结束')
     self.db.execute('UPDATE devices SET enabled=0 WHERE id=?',(d['id'],));self.db.execute('INSERT OR REPLACE INTO scopes VALUES(?,?,0,?)',(d['id'],clock(),''));self.db.execute('UPDATE schedules SET enabled=0 WHERE device_id=?',(d['id'],));self.db.commit();self.report_cache.clear();return {'ok':True}
    if path=='/api/channels':
     self.require(user,('admin',));kind=b.get('kind');c=b.get('config',{})
     if b.get('id'):
      old=self.one('SELECT config FROM channels WHERE id=?',(b['id'],))
      if old:
       from protected import transform
       old_config=json.loads(transform(old['config'],False))
       for secret_key in ('password','secret'):
        if not c.get(secret_key) and old_config.get(secret_key):c[secret_key]=old_config[secret_key]
     if kind not in ('webhook','feishu','smtp'):raise Error('通知类型无效')
     if kind in ('webhook','feishu'):
      u=urllib.parse.urlsplit(str(c.get('url','')))
      if u.scheme not in ('https','http') or not u.hostname or u.username:raise Error('Webhook 地址无效')
      if kind=='feishu' and (u.scheme!='https' or u.hostname not in ('open.feishu.cn','open.larksuite.com') or not u.path.startswith('/open-apis/bot/v2/hook/')):raise Error('请填写飞书或 Lark 自定义机器人的 HTTPS Webhook 地址')
      if len(str(c.get('secret',''))) > 512 or any(x in str(c.get('secret','')) for x in '\r\n'):raise Error('飞书签名密钥格式无效')
     else:
      for key in ('host','sender','recipients'):text(c.get(key),1000)
      number(c.get('port',465),1,65535)
     from protected import transform
     cid=b.get('id') or uuid.uuid4().hex;self.db.execute('INSERT INTO channels VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,config=excluded.config,enabled=excluded.enabled',(cid,text(b.get('name')),kind,transform(jdump(c).encode(),True),int(b.get('enabled',True))));self.db.commit();return {'id':cid}
    if path=='/api/channel-test':
     self.require(user,('admin',));cid=text(b.get('id'))
     if not self.one('SELECT id FROM channels WHERE id=?',(cid,)):raise Error('渠道不存在',404)
     now=clock();did=uuid.uuid4().hex;self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)',(did,cid,None,now,'queued',0,now,'',jdump({'event_id':did,'title':'通知测试','message':'天枢通知测试','level':'info'})));self.db.commit();self.notification_tick();return {'ok':True,'delivery_id':did}
    if path=='/api/delivery-retry':
     self.require(user,('admin',));self.db.execute("UPDATE deliveries SET state='queued',attempts=0,next_try=? WHERE id=?",(clock(),text(b.get('id'))));self.db.commit();return {'ok':True}
    if path=='/api/member-update':
     self.require(user,('admin',));uid=text(b.get('id'));role=b.get('role')
     if role not in ('viewer','operator','admin','disabled'):raise Error('角色无效')
     old=self.one('SELECT * FROM users WHERE id=?',(uid,))
     if not old:raise Error('账户不存在',404)
     if old['role']=='admin' and role!='admin' and self.one("SELECT COUNT(*) n FROM users WHERE role='admin'")['n']<=1:raise Error('必须保留至少一个管理员')
     self.db.execute('UPDATE users SET role=? WHERE id=?',(role,uid))
     if b.get('password'):
      if not 12<=len(b['password'])<=200:raise Error('密码应为12至200个字符')
      self.db.execute('UPDATE users SET password=? WHERE id=?',(self.hash_password(b['password']),uid))
     self.db.execute('DELETE FROM space_members WHERE user_id=?',(uid,))
     for space in b.get('spaces',[]):self.space(space);self.db.execute('INSERT INTO space_members VALUES(?,?)',(space,uid))
     self.sessions={k:v for k,v in self.sessions.items() if v['user'].get('id')!=uid};self.db.commit();return {'ok':True}
    if path=='/api/gateway-update':
     self.require(user,('admin',));gid=text(b.get('id'));g=self.one('SELECT * FROM gateways WHERE id=?',(gid,))
     if not g:raise Error('网关不存在',404)
     token=secrets.token_urlsafe(36) if b.get('rotate') else None;self.db.execute('UPDATE gateways SET enabled=? WHERE id=?',(int(b.get('enabled',True)),gid))
     if token:self.db.execute('UPDATE gateways SET key_hash=? WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),gid))
     self.db.commit();return {'ok':True,'token':token}
    if path=='/api/backend-settings':
     self.require(user,('admin',));port=int(number(b.get('port',0),0,65535));logo=b.get('logo');settings={'port':port}
     if logo:
      if not isinstance(logo,str) or not logo.startswith(('data:image/png;base64,','data:image/jpeg;base64,','data:image/webp;base64,')) or len(logo)>180000:raise Error('Logo 请使用小于128KB的 PNG、JPEG 或 WebP')
      base64.b64decode(logo.split(',',1)[1],validate=True);settings['logo']=logo
     self.db.execute("INSERT OR REPLACE INTO settings VALUES('backend',?)",(jdump(settings),));self.db.commit();return {'ok':True,'restart_required':True}
    if path=='/api/restore':
     self.require(user,('admin',))
     if b.get('confirmation')!='恢复备份':raise Error('请确认覆盖当前数据')
     if self.api.inflight or self.delivery_busy or self.ai.inflight:raise Error('后台任务尚未完成，请稍后重试',409)
     import sqlite3
     try:
      raw=base64.b64decode(b.get('data',''),validate=True)
      if len(raw)>32*1024*1024 or not raw.startswith(b'SQLite format 3\x00'):raise Error('备份格式无效')
      # Serialized WAL databases retain WAL header flags but no sidecar file.
      # This is a complete backup snapshot, so normalize its journal header for memory validation.
      raw=raw[:18]+bytes((1,1))+raw[20:];source=sqlite3.connect(':memory:');source.deserialize(raw)
      if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise Error('备份校验失败')
      required={'spaces','devices','users','scopes','tariff_versions','rules','channels','deliveries'};tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
      if not required<=tables:raise Error('请使用 v1.0 完整备份')
      source.backup(self.db);source.close();self.sessions.clear();self.streams.clear();self.report_cache.clear()
      from ai_analysis import AIAnalysis
      self.ai=AIAnalysis(self);return {'ok':True}
     except (sqlite3.Error,ValueError):raise Error('无法读取备份数据库')
    if path=='/api/reset-preview':
     self.require(user,('admin',));token=secrets.token_urlsafe(24);self.reset_tokens[token]=clock()+120;tables=[r['name'] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")];return {'token':token,'records':{table:self.one('SELECT COUNT(*) n FROM "'+table+'"')['n'] for table in tables},'confirmation':'恢复出厂设置'}
    if path=='/api/factory-reset':
     self.require(user,('admin',));expires=self.reset_tokens.pop(b.get('token',''),0)
     if expires<clock() or b.get('confirmation')!='恢复出厂设置':raise Error('请重新确认删除范围',409)
     account=self.one('SELECT * FROM users WHERE id=?',(user.get('id'),))
     if not account or not self.verify_password(str(b.get('password','')),account['password']):raise Error('管理员密码错误',403)
     if self.api.inflight or self.delivery_busy or self.ai.inflight:raise Error('仍有查询、AI 或通知正在执行，请稍后重试',409)
     tables=[r['name'] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
     for table in tables:
      if table!='spaces':self.db.execute('DELETE FROM "'+table+'"')
     self.db.execute('DELETE FROM spaces');sid=uuid.uuid4().hex;self.db.execute('INSERT INTO spaces VALUES(?,?,?,?,?)',(sid,'我的用电空间','',jdump(tariff_default()),clock()));self.db.execute('INSERT INTO tariff_versions VALUES(?,?,?)',(sid,0,jdump(tariff_default())));self.db.commit();self.sessions.clear();self.streams.clear();self.report_cache.clear();return {'ok':True,'requires_setup':True}
   result=super().dispatch(method,path,user,b,params)
   if path=='/api/system' and method=='GET':
    for member in result['users']:member['spaces']=[r['space_id'] for r in self.rows('SELECT space_id FROM space_members WHERE user_id=?',(member['id'],))]
   return result
