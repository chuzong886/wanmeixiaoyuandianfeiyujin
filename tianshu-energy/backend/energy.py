"""Tianshu Energy: local SQLite energy management, extended with the original campus module."""
from __future__ import annotations
import collections, contextlib, datetime as dt, functools, hashlib, hmac, json, math, secrets, sqlite3, statistics, threading, time, uuid
from pathlib import Path
TZ=dt.timezone(dt.timedelta(hours=8)); VERSION='1.0.0'
def clock(): return time.time()
def local(ts=None): return dt.datetime.fromtimestamp(clock() if ts is None else ts,TZ)
def jdump(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def obj(row): return dict(row) if row else None
class Error(Exception):
 def __init__(self,message,status=400):super().__init__(message);self.status=status

def atomic(fn):
 @functools.wraps(fn)
 def wrapped(self,*args,**kwargs):
  with self.lock:
   try:return fn(self,*args,**kwargs)
   except BaseException:
    self.db.rollback();raise
 return wrapped

def number(value,low=0,high=1e12):
 try:n=float(value)
 except (ValueError,TypeError):raise Error('请输入有效数值')
 if not math.isfinite(n) or not low<=n<=high:raise Error(f'数值应在 {low:g} 至 {high:g} 之间')
 return n

def text(value,limit=100,required=True):
 s=str(value or '').strip()
 if (required and not s) or len(s)>limit or any(ord(c)<32 for c in s):raise Error('文本为空、过长或含无效字符')
 return s

def tariff_default():
 return {'prices':[0.38]*7+[0.72]*3+[1.12]*2+[0.72]*3+[1.12]*3+[1.38]*3+[0.72]*2+[0.38], 'bands':['谷']*7+['平']*3+['峰']*2+['平']*3+['峰']*3+['尖']*3+['平']*2+['谷'],'monthly_budget':12000,'currency':'CNY','note':'示例电价，请按当地实际合同填写；结果为估算费用。'}

SCHEMA='''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',tariff TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,space_id TEXT NOT NULL REFERENCES spaces(id),name TEXT NOT NULL,area TEXT NOT NULL,kind TEXT NOT NULL,source TEXT NOT NULL CHECK(source IN ('simulated','gateway','third_party')),gateway_id TEXT,nominal_w REAL NOT NULL,threshold_w REAL NOT NULL,protected INTEGER NOT NULL DEFAULT 0,controllable INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,relay_on INTEGER NOT NULL DEFAULT 1,power_w REAL NOT NULL DEFAULT 0,voltage REAL NOT NULL DEFAULT 220,pf REAL NOT NULL DEFAULT .95,last_seen REAL NOT NULL DEFAULT 0,last_sample REAL NOT NULL DEFAULT 0,total_kwh REAL NOT NULL DEFAULT 0,sequence INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS samples(device_id TEXT NOT NULL,ts REAL NOT NULL,power_w REAL NOT NULL,voltage REAL NOT NULL,pf REAL NOT NULL,PRIMARY KEY(device_id,ts));
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS hourly(device_id TEXT NOT NULL,hour INTEGER NOT NULL,energy_wh REAL NOT NULL,coverage_s REAL NOT NULL,simulated INTEGER NOT NULL,PRIMARY KEY(device_id,hour));
CREATE INDEX IF NOT EXISTS hourly_hour ON hourly(hour);
CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,space_id TEXT NOT NULL,device_id TEXT NOT NULL,desired INTEGER NOT NULL,state TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,finished REAL,detail TEXT NOT NULL DEFAULT '',idempotency TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,space_id TEXT NOT NULL,device_id TEXT,key TEXT NOT NULL,level TEXT NOT NULL,title TEXT NOT NULL,message TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,ack_by TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS active_alert ON alerts(key) WHERE state IN ('active','acknowledged');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cameras(id TEXT PRIMARY KEY,space_id TEXT NOT NULL,name TEXT NOT NULL,area TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,space_id TEXT NOT NULL,device_id TEXT NOT NULL,at TEXT NOT NULL,desired INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,last_run TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS gateways(id TEXT PRIMARY KEY,name TEXT NOT NULL,space_id TEXT NOT NULL,key_hash TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,last_seen REAL NOT NULL DEFAULT 0);
'''
class Energy:
 def __init__(self,directory,seed=True):
  self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
  self.db=sqlite3.connect(self.directory/'energy.sqlite',check_same_thread=False);self.db.row_factory=sqlite3.Row
  self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA foreign_keys=ON');self.db.executescript(SCHEMA)
  # Transactional migration keeps existing devices, samples, history and identifiers.
  table_sql=self.db.execute("SELECT sql FROM sqlite_master WHERE name='devices'").fetchone()[0]
  if "'third_party'" not in table_sql:
   migrated=table_sql.replace('CREATE TABLE devices','CREATE TABLE devices_v2').replace('CREATE TABLE "devices"','CREATE TABLE devices_v2').replace("'simulated','gateway'","'simulated','gateway','third_party'")
   with self.db:
    self.db.execute('BEGIN IMMEDIATE')
    self.db.execute(migrated);self.db.execute('INSERT INTO devices_v2 SELECT * FROM devices');self.db.execute('DROP TABLE devices');self.db.execute('ALTER TABLE devices_v2 RENAME TO devices')
  columns={r['name'] for r in self.db.execute('PRAGMA table_info(devices)')}
  for name,declaration in [('phases','INTEGER NOT NULL DEFAULT 1'),('nominal_voltage','REAL NOT NULL DEFAULT 220'),('counted','INTEGER NOT NULL DEFAULT 1')]:
   if name not in columns:self.db.execute(f'ALTER TABLE devices ADD COLUMN {name} {declaration}')
  self.sessions={};self.streams={};self.report_cache={};self.stop=threading.Event();self.last_tick=0;self.ticks=0;self.last_error=None
  with self.lock:
   self.db.execute("UPDATE commands SET state='unknown', detail='进程重启，执行结果未确认，不自动重发' WHERE state='dispatched'")
   if seed and not self.db.execute('SELECT 1 FROM spaces').fetchone():self.seed()
   self.db.commit()
  from thirdparty import ThirdParty
  self.api=ThirdParty(self)
 def rows(self,q,args=()):return [dict(x) for x in self.db.execute(q,args).fetchall()]
 def one(self,q,args=()):return obj(self.db.execute(q,args).fetchone())
 def audit(self,actor,action,target,detail):self.db.execute('INSERT INTO audit(ts,actor,action,target,detail) VALUES(?,?,?,?,?)',(clock(),actor,action,str(target),detail))
 def seed(self):
  now=clock();sid='space-demo';tariff=tariff_default()
  self.db.execute('INSERT INTO spaces VALUES(?,?,?,?,?)',(sid,'示范用电空间','名称、区域和设备均可自定义，适用于任意用电环境。',jdump(tariff),now))
  templates=[('air','空调回路','环境调节','circuit',8200,9700,0),('lights','照明回路','公共区域','circuit',4200,5600,0),('power','动力设备','设备区域','appliance',12500,14500,0),('sockets','插座回路','工作区域','circuit',2600,3800,0),('water','热水设备','生活区域','appliance',3600,4600,0),('essential','关键设备回路','保障区域','circuit',1200,1800,1)]
  for index,(device,name,area,kind,watts,threshold,protected) in enumerate(templates):
   self.db.execute('INSERT INTO devices(id,space_id,name,area,kind,source,nominal_w,threshold_w,protected,controllable) VALUES(?,?,?,?,?,?,?,?,?,?)',(device,sid,name,area,kind,'simulated',watts,threshold,protected,not protected))
   records=[];last_hour=int(now//3600)*3600
   for hour in range(last_hour-35*86400,last_hour,3600):
    value=self.profile(index,hour+1800)*watts;records.append((device,hour,value,3600,1))
   self.db.executemany('INSERT INTO hourly VALUES(?,?,?,?,?)',records)
   total=sum(x[2] for x in records)/1000
   self.db.execute('UPDATE devices SET total_kwh=? WHERE id=?',(total,device))
  for i,(name,area) in enumerate([('用电空间全景','设备区域'),('配电区域','保障区域')]):self.db.execute('INSERT INTO cameras VALUES(?,?,?,?,?,?,?)',(f'cam-demo-{i}',sid,name,area,'illustration','',1))
  self.audit('system','initialize',sid,'创建合成演示空间；历史、设备、功率和场景示意均非真实数据')
 def profile(self,index,ts):
  h=local(ts).hour+local(ts).minute/60;weekday=local(ts).weekday()<5
  if index%6==5:return .68+.025*math.sin(ts/500)
  base=.18 if h<7 or h>=23 else .73 if 8<=h<18 else .48
  if index%6==4:base=.86 if 6<=h<9 or 18<=h<22 else .12
  if index%6==2:base*=1.0 if weekday else .65
  return max(.04,base+.13*math.sin(h*.7+index)+.035*math.sin(ts/19+index))
 def make_session(self,user):
  sid=secrets.token_urlsafe(32);session={'user':user,'csrf':secrets.token_urlsafe(24),'expires':clock()+8*3600};self.sessions[sid]=session;return sid,session
 def get_session(self,sid):
  s=self.sessions.get(sid or '')
  if not s or s['expires']<clock():raise Error('请重新登录',401)
  return s
 def boot(self,sid):
  with self.lock:
   owner=bool(self.db.execute('SELECT 1 FROM users LIMIT 1').fetchone())
   s=self.sessions.get(sid or '');new=None
   self.sessions={k:v for k,v in self.sessions.items() if v['expires']>clock()}
   if not s or s['expires']<clock():
    if owner:return {'user':None,'owner_configured':True,'version':VERSION},None
    new,s=self.make_session({'id':'demo','username':'演示管理员','role':'demo'})
   return {'user':s['user'],'csrf':s['csrf'],'owner_configured':owner,'version':VERSION},new
 def login(self,body):
  with self.lock:
   user=self.one('SELECT * FROM users WHERE username=?',(str(body.get('username','')),));password=str(body.get('password',''))
   if not user or not self.verify_password(password,user['password']):raise Error('用户名或密码错误',401)
   sid,s=self.make_session({k:user[k] for k in ['id','username','role']});self.audit(user['username'],'login','session','登录本地管理平台');self.db.commit();return {'user':s['user'],'csrf':s['csrf']},sid
 @staticmethod
 def hash_password(password):
  salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex();return f'{salt}:{digest}'
 @staticmethod
 def verify_password(password,encoded):
  salt,digest=encoded.split(':');return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex(),digest)
 def require(self,user,roles=('admin','operator','demo')):
  if user['role'] not in roles:raise Error('当前账户没有此操作权限',403)
 def space(self,sid):
  s=self.one('SELECT * FROM spaces WHERE id=?',(sid,))
  if not s:raise Error('用电空间不存在',404)
  s['tariff']=json.loads(s['tariff']);return s
 def spaces(self):return [self.space(x['id']) for x in self.rows('SELECT id FROM spaces ORDER BY created')]
 def device(self,did):
  d=self.one('SELECT * FROM devices WHERE id=? AND enabled=1',(did,))
  if not d:raise Error('设备不存在或已停用',404)
  return d
 def devices(self,sid):
  now=clock();out=self.rows('SELECT * FROM devices WHERE space_id=? AND enabled=1 ORDER BY protected,id',(sid,))
  for d in out:
   d['online']=0<=now-d['last_sample']<10;d['current_a']=d['power_w']/(d['voltage']*d['pf']*(math.sqrt(3) if d['phases']==3 else 1)) if d['voltage']>0 and d['pf']>0 else None;d['protected']=bool(d['protected']);d['relay_on']=bool(d['relay_on']);d['controllable']=bool(d['controllable'])
   d['has_power']=True;d['has_voltage']=True
   if d['source']=='third_party':self.api.public(d)
  return out
 def _energy_rows(self,sid,start,end):return self.rows('SELECT h.hour,SUM(h.energy_wh)/1000 AS kwh,MAX(h.coverage_s) coverage_s FROM hourly h JOIN devices d ON d.id=h.device_id WHERE d.space_id=? AND d.counted=1 AND h.hour>=? AND h.hour<? GROUP BY h.hour ORDER BY h.hour',(sid,start,end))
 def totals(self,sid,now):
  s=self.space(sid);day=local(now).replace(hour=0,minute=0,second=0,microsecond=0);month=day.replace(day=1);next_month=(month.replace(day=28)+dt.timedelta(days=4)).replace(day=1);prices=s['tariff']['prices'];rows=self._energy_rows(sid,month.timestamp(),now+3600)
  today_kwh=today_cost=month_kwh=month_cost=0
  for r in rows:
   cost=r['kwh']*prices[local(r['hour']).hour];month_kwh+=r['kwh'];month_cost+=cost
   if r['hour']>=day.timestamp():today_kwh+=r['kwh'];today_cost+=cost
  recent=self._energy_rows(sid,(day-dt.timedelta(days=7)).timestamp(),day.timestamp());days=collections.defaultdict(float);coverage=collections.defaultdict(float)
  for r in recent:
   day_key=local(r['hour']).date().isoformat();days[day_key]+=r['kwh']*prices[local(r['hour']).hour];coverage[day_key]+=min(1,r['coverage_s']/3600)
  days={day:value for day,value in days.items() if coverage[day]>=23.5}
  average=statistics.mean(days.values()) if days else 0;remaining=(next_month-day).days-(now-day.timestamp())/86400;estimate=month_cost+max(0,remaining)*average
  return {'today_kwh':today_kwh,'today_cost':today_cost,'month_kwh':month_kwh,'month_cost':month_cost,'forecast_cost':estimate,'forecast_low':max(month_cost,estimate*.8),'forecast_high':estimate*1.2,'forecast_days':len(days),'forecast_available':len(days)>=3,'budget':s['tariff']['monthly_budget'],'budget_percent':month_cost/s['tariff']['monthly_budget']*100 if s['tariff']['monthly_budget']>0 else 0,'price':prices[local(now).hour],'band':s['tariff']['bands'][local(now).hour]}
 def snapshot(self,sid):
  with self.lock:
   now=clock();s=self.space(sid);devices=self.devices(sid);power=sum(d['power_w'] for d in devices if d['online'] and d['counted'] and d.get('has_power',True));totals=self.totals(sid,now)
   available=any(d['online'] and d['counted'] and d['has_power'] for d in devices);complete=all(d['online'] and d['has_power'] for d in devices if d['counted'])
   return {'now':now,'space':s,'totals':totals,'devices':devices,'power_kw':power/1000,'power_available':available,'power_complete':complete,'online':sum(d['online'] for d in devices),'device_count':len(devices),'source':'simulated' if all(d['source']=='simulated' for d in devices) else 'mixed' if any(d['source']=='simulated' for d in devices) else 'third_party' if all(d['source']=='third_party' for d in devices) else 'mixed' if any(d['source']=='third_party' for d in devices) else 'gateway','heartbeat':self.last_tick,'trend':list(self.streams.get(sid,[])),'alerts':self.rows("SELECT * FROM alerts WHERE space_id=? AND state IN ('active','acknowledged') ORDER BY created DESC LIMIT 10",(sid,)),'commands':self.rows('SELECT c.*,d.name device_name FROM commands c JOIN devices d ON d.id=c.device_id WHERE c.space_id=? ORDER BY c.created DESC LIMIT 8',(sid,))}
 def reports(self,sid,days=7):
  days=max(1,min(60,int(days)));now=clock();key=(sid,days)
  with self.lock:
   cached=self.report_cache.get(key)
   if cached and now-cached[0]<30:return cached[1]
   s=self.space(sid);start=local(now).replace(hour=0,minute=0,second=0,microsecond=0)-dt.timedelta(days=days-1);rows=self._energy_rows(sid,start.timestamp(),now+3600);prices=s['tariff']['prices'];daily={};by_hour=[0.0]*24;heat=[[0.0]*24 for _ in range(7)];heat_n=[[0]*24 for _ in range(7)]
   for i in range(days):daily[(start+dt.timedelta(days=i)).date().isoformat()]={'date':(start+dt.timedelta(days=i)).date().isoformat(),'kwh':0,'cost':0,'covered_hours':0}
   for r in rows:
    date=local(r['hour']);h=date.hour;day=daily[date.date().isoformat()];day['kwh']+=r['kwh'];day['cost']+=r['kwh']*prices[h];day['covered_hours']+=min(1,r['coverage_s']/3600);by_hour[h]+=r['kwh'];heat[date.weekday()][h]+=r['kwh'];heat_n[date.weekday()][h]+=1
   for w in range(7):
    for h in range(24):heat[w][h]=heat[w][h]/heat_n[w][h] if heat_n[w][h] else None
   rank=self.rows('SELECT d.id,d.name,d.area,d.source,SUM(h.energy_wh)/1000 kwh FROM devices d JOIN hourly h ON h.device_id=d.id WHERE d.space_id=? AND d.counted=1 AND h.hour>=? GROUP BY d.id ORDER BY kwh DESC',(sid,start.timestamp()))
   bands={k:{'band':k,'kwh':0,'cost':0} for k in ['谷','平','峰','尖']}
   for r in rows:b=bands[s['tariff']['bands'][local(r['hour']).hour]];b['kwh']+=r['kwh'];b['cost']+=r['kwh']*prices[local(r['hour']).hour]
   peak_hours=sorted(range(24),key=lambda h:by_hour[h],reverse=True)[:3];total=sum(by_hour)
   out={'days':days,'daily':list(daily.values()),'hourly':[{'hour':h,'kwh':by_hour[h]} for h in range(24)],'heat':heat,'ranking':rank,'bands':list(bands.values()),'peak_hours':peak_hours,'peak_share':sum(by_hour[h] for h in peak_hours)/total if total else 0,'total_kwh':total,'estimated_shift_saving':sum(by_hour[h] for h in peak_hours)*.1*(max(prices)-min(prices)),'note':'分时费用按当前配置电价重新估算，不作为电网结算账单；移峰建议假定可转移高峰时段10%的用量，非保证节省。'};self.report_cache[key]=(now,out);return out
 def alert(self,key,sid,did,level,title,message,condition,now):
  row=self.one("SELECT * FROM alerts WHERE key=? AND state IN ('active','acknowledged')",(key,))
  if condition:
   if row:self.db.execute('UPDATE alerts SET message=?,updated=? WHERE id=?',(message,now,row['id']))
   else:self.db.execute('INSERT INTO alerts(id,space_id,device_id,key,level,title,message,state,created,updated,ack_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,sid,did,key,level,title,message,'active',now,now,None))
  elif row:self.db.execute("UPDATE alerts SET state='resolved',updated=? WHERE id=?",(now,row['id']))
 def check_alerts(self,now):
  for s in self.spaces():
   for d in self.devices(s['id']):
    if d['source']=='third_party':
     if d.get('api',{}).get('configuration_error'):
      self.alert('api-offline:'+d['id'],s['id'],d['id'],'warning','第三方配置需要重新填写',d['api']['status'],True,now);continue
     self.alert('overload:'+d['id'],s['id'],d['id'],'warning','接口功率超过阈值',f"{d['name']} 最近上报功率超过设备设置阈值，请核实",d['online'] and d['has_power'] and d['power_w']>d['threshold_w'],now)
     reading=d.get('api',{}).get('reading');c=self.api.config(d['id']);balance=reading.get('balance') if reading else None;fresh=d['online'];f=d.get('api',{}).get('forecast',{}).get('forecast',{})
     if fresh and balance is not None:
      self.alert('api-low:'+d['id'],s['id'],d['id'],'warning','剩余电量偏低',f"{d['name']} 最近剩余 {balance} 度，读数按第三方查询计划更新",c['critical_kwh']<balance<=c['warning_kwh'],now)
      self.alert('api-critical:'+d['id'],s['id'],d['id'],'critical','剩余电量紧急',f"{d['name']} 最近剩余 {balance} 度，请核实并及时充值",balance<=c['critical_kwh'],now)
      self.alert('api-forecast:'+d['id'],s['id'],d['id'],'warning','电量预计即将耗尽',f"{d['name']} 预计可用约 {f.get('remaining_hours',0):.1f} 小时，属于本地保守估计",f.get('available',False) and f.get('remaining_hours',1e9)<=c['forecast_notice_hours'],now)
     self.alert('api-offline:'+d['id'],s['id'],d['id'],'warning','第三方读数待更新',d.get('api',{}).get('status','暂无读数'),not fresh and bool(reading or d['api']['last_attempt']),now)
     continue
    self.alert('overload:'+d['id'],s['id'],d['id'],'warning','功率超过预设阈值',f"{d['name']} 当前 {d['power_w']/1000:.2f} kW，阈值 {d['threshold_w']/1000:.2f} kW",d['online'] and d['power_w']>d['threshold_w'],now)
    self.alert('offline:'+d['id'],s['id'],d['id'],'warning','设备数据中断',f"{d['name']} 已超过 10 秒没有上报；当前功率未知",not d['online'] and d['source']=='gateway',now)
    self.alert('voltage:'+d['id'],s['id'],d['id'],'critical','电压超出监测范围',f"{d['name']} 上报电压 {d['voltage']:.1f} V，请现场核实",d['online'] and not d['nominal_voltage']*.9<=d['voltage']<=d['nominal_voltage']*1.1,now)
   totals=self.totals(s['id'],now)
   self.alert('budget:'+s['id'],s['id'],None,'warning','月度费用预计超出预算',f"预计 ¥{totals['forecast_cost']:.0f}，预算 ¥{totals['budget']:.0f}。这是当前电价和历史用量下的参考估算。",totals['forecast_available'] and totals['budget']>0 and totals['forecast_cost']>totals['budget'],now)
 def add_sample(self,d,power,voltage,pf,relay,ts,elapsed):
  self.db.execute('UPDATE devices SET power_w=?,voltage=?,pf=?,relay_on=?,last_seen=?,last_sample=?,total_kwh=total_kwh+? WHERE id=?',(power,voltage,pf,int(relay),clock(),ts,power*elapsed/3600000,d['id']))
  self.db.execute('INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)',(d['id'],ts,power,voltage,pf))
  # Split interval energy over tariff-hour boundaries; never charge a whole gap at the latest hour.
  begin=ts-elapsed
  while begin<ts:
   hour=int(begin//3600)*3600;end=min(ts,hour+3600);seconds=end-begin;wh=power*seconds/3600
   self.db.execute('INSERT INTO hourly VALUES(?,?,?,?,?) ON CONFLICT(device_id,hour) DO UPDATE SET energy_wh=energy_wh+excluded.energy_wh,coverage_s=coverage_s+excluded.coverage_s',(d['id'],hour,wh,seconds,int(d['source']=='simulated')));begin=end
 @atomic
 def tick(self,now=None):
  now=clock() if now is None else now
  with self.lock:
   pending=self.rows("SELECT * FROM commands WHERE state IN ('queued','dispatched')")
   for c in pending:
    if c['expires']<=now:self.db.execute("UPDATE commands SET state='expired',finished=?,detail='指令超时；未把请求当作设备已执行' WHERE id=?",(now,c['id']));continue
    d=self.device(c['device_id'])
    if d['source']=='simulated' and now-c['created']>=.8:
     if d['protected']:self.db.execute("UPDATE commands SET state='rejected',finished=?,detail='回路保护阻止执行' WHERE id=?",(now,c['id']))
     else:self.db.execute('UPDATE devices SET relay_on=? WHERE id=?',(c['desired'],d['id']));self.db.execute("UPDATE commands SET state='confirmed',finished=?,detail='模拟继电器已回报状态' WHERE id=?",(now,c['id']));self.audit('simulator','command_confirmed',d['id'],c['id'])
   for i,d in enumerate(self.rows("SELECT * FROM devices WHERE source='simulated' AND enabled=1 ORDER BY id")):
    if d.get('device_type')=='appliance':continue
    elapsed=now-d['last_sample'] if d['last_sample'] else 1;elapsed=elapsed if 0<elapsed<=5 else 1
    # Stable per-device waveform: disabling a load affects the next one-second sample.
    index=int(hashlib.sha256(d['id'].encode()).hexdigest()[:4],16)%5
    if d['protected']:index=5
    power=d['nominal_w']*self.profile(index,now) if d['relay_on'] else 0
    self.add_sample(d,power,d['nominal_voltage']*(1+.008*math.sin(now/15+i)),.92+.025*math.sin(now/40+i),d['relay_on'],now,elapsed)
   self.db.execute('UPDATE devices SET power_w=0 WHERE source=? AND last_seen<?',('gateway',now-10))
   for row in self.rows('SELECT * FROM schedules WHERE enabled=1'):
    if 'weekdays' in row and str(local(now).weekday()) not in row['weekdays'].split(','):continue
    day=local(now).date().isoformat();at=local(now).strftime('%H:%M')
    if at==row['at'] and row['last_run']!=day:
     d=self.device(row['device_id']);self.db.execute('UPDATE schedules SET last_run=? WHERE id=?',(day,row['id']))
     if d['source']=='simulated' and not d['protected'] and d['controllable'] and bool(d['relay_on'])!=bool(row['desired']):
      try:
       self.command({'id':'scheduler','username':'计划任务','role':'operator'}, {'device_id':d['id'],'desired':bool(row['desired']),'reason':'已启用的每日计划','confirm_name':d['name'],'idempotency':row['id']+day},from_schedule=True)
       if 'last_result' in row:self.db.execute('UPDATE schedules SET last_result=? WHERE id=?',('已下发，等待回执',row['id']))
      except Error as error:
       self.audit('scheduler','schedule_failed',row['id'],str(error))
       if 'last_result' in row:self.db.execute('UPDATE schedules SET last_result=? WHERE id=?',(str(error),row['id']))
     elif 'last_result' in row:self.db.execute('UPDATE schedules SET last_result=? WHERE id=?',('跳过：状态已满足或当前禁止控制',row['id']))
   self.ticks+=1
   if self.ticks%5==1:self.check_alerts(now)
   if self.ticks%600==0:self.db.execute('DELETE FROM samples WHERE ts<?',(now-6*3600,));self.db.execute('DELETE FROM hourly WHERE hour<?',(now-400*86400,))
   self.db.commit();self.last_tick=now;self.last_error=None
   for s in self.spaces():
    visible=self.devices(s['id']);known=any(d['online'] and d['counted'] and d['has_power'] for d in visible);power=sum(d['power_w'] for d in visible if d['online'] and d['counted'] and d['has_power'])/1000;self.streams.setdefault(s['id'],collections.deque(maxlen=1800)).append({'ts':now,'kw':round(power,4) if known else None})
 def run(self):
  while not self.stop.is_set():
   started=time.monotonic()
   try:self.tick();self.api.schedule()
   except Exception as error:
    self.last_error={'time':clock(),'type':type(error).__name__,'message':'采集处理失败，请检查本地数据与服务日志'}
    try:
     with (self.directory/'runtime.log').open('a',encoding='utf-8') as log:log.write(f'{local().isoformat()} worker_error {type(error).__name__}\n')
    except OSError:pass
   self.stop.wait(max(.1,1-(time.monotonic()-started)))
 @atomic
 def command(self,user,b,from_schedule=False):
  self.require(user);d=self.device(text(b.get('device_id')));desired=b.get('desired')
  if type(desired) is not bool:raise Error('开关目标必须为明确的开或关')
  if not d['controllable'] or d['protected']:raise Error('该回路已设为保护设备，禁止远程开关',409)
  if user['role']=='demo' and d['source']!='simulated':raise Error('演示账户不能控制真实设备',403)
  if clock()-d['last_sample']>10:raise Error('设备数据已过期，无法确认当前状态',409)
  if b.get('confirm_name')!=d['name']:raise Error('请准确输入设备名称以确认操作')
  reason=text(b.get('reason'),200);idem=text(b.get('idempotency'),100)
  old=self.one('SELECT * FROM commands WHERE idempotency=?',(idem,))
  if old:
   if old['device_id']!=d['id'] or old['desired']!=int(desired):raise Error('重复请求标识与操作内容不符',409)
   return old
  if self.one("SELECT id FROM commands WHERE device_id=? AND state IN ('queued','dispatched')",(d['id'],)):raise Error('此设备已有指令待确认，请等待执行回执',409)
  if bool(d['relay_on'])==desired:raise Error('设备已经处于目标状态，无需重复下发',409)
  cid=uuid.uuid4().hex;now=clock();self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,d['space_id'],d['id'],int(desired),'queued',user['username'],reason,now,now+30,None,'等待设备回执',idem))
  self.audit(user['username'],'switch_on' if desired else 'switch_off',d['id'],jdump({'command_id':cid,'reason':reason,'source':d['source']}));self.db.commit();return self.one('SELECT * FROM commands WHERE id=?',(cid,))
 def save_space(self,user,b):
  self.require(user,('admin','demo'));sid=b.get('id') or uuid.uuid4().hex;name=text(b.get('name'));description=text(b.get('description'),300,False)
  if b.get('id'):
   self.space(sid);self.db.execute('UPDATE spaces SET name=?,description=? WHERE id=?',(name,description,sid))
  else:self.db.execute('INSERT INTO spaces VALUES(?,?,?,?,?)',(sid,name,description,jdump(tariff_default()),clock()))
  self.audit(user['username'],'save_space',sid,name);self.db.commit();return self.space(sid)
 @atomic
 def save_device(self,user,b):
  self.require(user,('admin','demo'));sid=text(b.get('space_id'));self.space(sid);did=b.get('id') or uuid.uuid4().hex
  source=b.get('source','simulated');gateway=b.get('gateway_id') or None
  if source not in ('simulated','gateway','third_party'):raise Error('设备数据源无效')
  if source=='third_party':
   self.require(user,('admin',))
   from thirdparty import validate
   validate(b.get('api_config'))
  if source=='gateway':
   self.require(user,('admin',));g=self.one('SELECT * FROM gateways WHERE id=? AND space_id=? AND enabled=1',(gateway,sid))
   if not g:raise Error('请先配置属于该空间的网关')
  name=text(b.get('name'));area=text(b.get('area'));kind=b.get('kind','circuit')
  if kind not in ('circuit','appliance','meter'):raise Error('设备类型无效')
  watts=number(b.get('nominal_w'),1,1e8);threshold=number(b.get('threshold_w'),1,1e8);protected=bool(b.get('protected',False));controllable=bool(b.get('controllable',True)) and not protected
  if source=='third_party':controllable=False
  phases=int(number(b.get('phases',1),1,3));nominal_voltage=number(b.get('nominal_voltage',220),1,1000);counted=bool(b.get('counted',True))
  if phases not in (1,3):raise Error('请选择单相或三相供电')
  if b.get('id'):
   old=self.device(did)
   if old['space_id']!=sid or old['source']!=source:raise Error('已有设备不能跨空间或切换数据源，请新建设备')
   if user['role']=='demo' and old['source']!='simulated':raise Error('演示账户不能编辑真实设备',403)
   if self.one("SELECT id FROM commands WHERE device_id=? AND state IN ('queued','dispatched')",(did,)):raise Error('请等待设备指令结束后修改配置',409)
   self.db.execute('UPDATE devices SET name=?,area=?,nominal_w=?,threshold_w=?,protected=?,controllable=?,gateway_id=? WHERE id=?',(name,area,watts,threshold,protected,controllable,gateway,did))
  else:self.db.execute('INSERT INTO devices(id,space_id,name,area,kind,source,gateway_id,nominal_w,threshold_w,protected,controllable) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(did,sid,name,area,kind,source,gateway,watts,threshold,protected,controllable))
  self.db.execute('UPDATE devices SET phases=?,nominal_voltage=?,counted=? WHERE id=?',(phases,nominal_voltage,counted,did))
  if source=='third_party':self.api.save(did,b['api_config'])
  self.report_cache.clear();self.audit(user['username'],'save_device',did,name);self.db.commit();return self.device(did)
 def save_tariff(self,user,b):
  self.require(user,('admin','demo'));sid=text(b.get('space_id'));s=self.space(sid);t=b.get('tariff',{})
  if len(t.get('prices',[]))!=24 or len(t.get('bands',[]))!=24:raise Error('请完整设置24小时电价')
  t['prices']=[number(x,0,100) for x in t['prices']]
  if any(x not in ['谷','平','峰','尖'] for x in t['bands']):raise Error('分时类型无效')
  t['monthly_budget']=number(t.get('monthly_budget'),0,1e12);t['currency']='CNY';t['note']=text(t.get('note'),300,False)
  self.db.execute('UPDATE spaces SET tariff=? WHERE id=?',(jdump(t),sid));self.report_cache.clear();self.audit(user['username'],'save_tariff',sid,'更新24小时分时电价与月度预算；分析费用按新价格重估');self.db.commit();return t
 def save_camera(self,user,b):
  self.require(user,('admin','demo'));sid=text(b.get('space_id'));self.space(sid);cid=b.get('id') or uuid.uuid4().hex;kind=b.get('kind','illustration');url=str(b.get('url','')).strip()
  if kind not in ('illustration','snapshot','mjpeg','video'):raise Error('支持场景示意、JPEG、MJPEG和浏览器支持的视频流')
  if kind!='illustration':
   from urllib.parse import urlsplit
   u=urlsplit(url)
   if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or len(url)>2000:raise Error('请提供不含用户名和密码的 HTTP/HTTPS 地址；RTSP 需要网关转换')
  if b.get('id') and not self.one('SELECT id FROM cameras WHERE id=? AND space_id=?',(cid,sid)):raise Error('摄像头不属于当前空间',404)
  self.db.execute('INSERT INTO cameras VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,area=excluded.area,kind=excluded.kind,url=excluded.url,enabled=excluded.enabled',(cid,sid,text(b.get('name')),text(b.get('area')),kind,url,1));self.audit(user['username'],'save_camera',cid,'更新监控画面配置，不保存凭据');self.db.commit();return self.one('SELECT * FROM cameras WHERE id=?',(cid,))
 def save_schedule(self,user,b):
  self.require(user,('admin','demo'));d=self.device(text(b.get('device_id')))
  if d['source'] not in ('simulated','gateway') or d['protected'] or not d['controllable']:raise Error('计划任务只允许具备开关能力的非保护模拟设备或 HTTP 网关设备')
  at=text(b.get('at'));dt.datetime.strptime(at,'%H:%M')
  if len(at)!=5 or type(b.get('desired')) is not bool:raise Error('计划时间或开关目标无效')
  uid=uuid.uuid4().hex;self.db.execute('INSERT INTO schedules(id,space_id,device_id,at,desired,enabled,last_run) VALUES(?,?,?,?,?,?,?)',(uid,d['space_id'],d['id'],at,int(b['desired']),1,''));self.audit(user['username'],'create_schedule',uid,f"{d['name']} 每日 {at}");self.db.commit();return {'id':uid}
 def setup(self,user,b):
  if self.db.execute('SELECT 1 FROM users').fetchone():raise Error('已配置管理员，请登录',409)
  name=text(b.get('username'));password=str(b.get('password',''))
  if len(password)<12 or len(password)>200:raise Error('密码长度应为12至200个字符')
  self.db.execute('INSERT INTO users VALUES(?,?,?,?)',(uuid.uuid4().hex,name,self.hash_password(password),'admin'));self.audit(name,'setup','owner','创建本机管理员');self.db.commit();self.sessions.clear();return {'ok':True,'message':'管理员已创建，请使用新账户登录'}
 def save_user(self,user,b):
  self.require(user,('admin',));name=text(b.get('username'));password=str(b.get('password',''));role=b.get('role')
  if role not in ('viewer','operator','admin') or not 12<=len(password)<=200:raise Error('请选择有效角色并使用至少12位密码')
  self.db.execute('INSERT INTO users VALUES(?,?,?,?)',(uuid.uuid4().hex,name,self.hash_password(password),role));self.audit(user['username'],'create_user',name,role);self.db.commit();return {'ok':True}
 def create_gateway(self,user,b):
  self.require(user,('admin',));sid=text(b.get('space_id'));self.space(sid);gid=uuid.uuid4().hex;key=secrets.token_urlsafe(36)
  self.db.execute('INSERT INTO gateways VALUES(?,?,?,?,?,?)',(gid,text(b.get('name')),sid,hashlib.sha256(key.encode()).hexdigest(),1,0));self.audit(user['username'],'create_gateway',gid,'建立HTTP网关接入令牌，仅显示一次');self.db.commit();return {'id':gid,'token':key}
 def gateway(self,token):
  key_hash=hashlib.sha256(token.encode()).hexdigest();g=self.one('SELECT * FROM gateways WHERE key_hash=? AND enabled=1',(key_hash,))
  if not g:raise Error('网关认证失败',401)
  return g
 @atomic
 def ingest(self,g,b):
  data=b.get('samples',[])
  if not isinstance(data,list) or not 1<=len(data)<=100:raise Error('一次上报1至100条样本')
  normalized=[];now=clock()
  for r in data:
   d=self.device(text(r.get('device_id')))
   if d['gateway_id']!=g['id'] or d['source']!='gateway':raise Error('设备不属于此网关',403)
   ts=number(r.get('ts'),now-120,now+5);seq=r.get('sequence')
   if type(seq) is not int or seq<1:raise Error('样本序号必须是从1开始的正整数')
   if type(r.get('relay_on')) is not bool:
    if hasattr(self,'capabilities') and not self.capabilities(d)['switch']:r={**r,'relay_on':False}
    else:raise Error('继电器状态必须为布尔值')
   normalized.append((d,r,ts,seq,number(r.get('power_w'),0,1e8)*d.get('scale',1),number(r.get('voltage'),0,1000),number(r.get('pf',1),.01,1)))
  accepted=0
  for d,r,ts,seq,p,v,pf in normalized:
   current=self.device(d['id'])
   if seq<=current['sequence'] or ts<=current['last_sample']:continue
   elapsed=ts-current['last_sample'] if current['last_sample'] else 0;elapsed=elapsed if 0<elapsed<=10 else 0
   relay=r.get('relay_on')
   if type(relay) is not bool:raise Error('继电器状态必须为布尔值')
   latest=self.one('SELECT MAX(finished) ts FROM commands WHERE device_id=? AND finished IS NOT NULL',(d['id'],))
   if latest and latest['ts'] and ts<latest['ts']:relay=bool(current['relay_on'])
   self.add_sample(current,p,v,pf,relay,ts,elapsed);self.db.execute('UPDATE devices SET sequence=? WHERE id=?',(seq,d['id']));accepted+=1
  self.db.execute('UPDATE gateways SET last_seen=? WHERE id=?',(now,g['id']));self.report_cache.clear();self.db.commit();return {'accepted':accepted}
 @atomic
 def gateway_commands(self,g):
  now=clock();rows=self.rows("SELECT c.* FROM commands c JOIN devices d ON d.id=c.device_id WHERE d.gateway_id=? AND c.state='queued' AND c.expires>?",(g['id'],now))
  for c in rows:self.db.execute("UPDATE commands SET state='dispatched',detail='网关已领取，等待设备执行回执' WHERE id=?",(c['id'],))
  self.db.execute('UPDATE gateways SET last_seen=? WHERE id=?',(now,g['id']));self.db.commit();return {'commands':rows}
 @atomic
 def gateway_ack(self,g,b):
  c=self.one('SELECT c.*,d.gateway_id FROM commands c JOIN devices d ON d.id=c.device_id WHERE c.id=?',(text(b.get('command_id')),))
  if not c or c['gateway_id']!=g['id']:raise Error('指令不属于此网关',403)
  if c['state'] in ('confirmed','rejected'):return {'state':c['state']}
  if c['state']!='dispatched' or clock()>c['expires']:raise Error('指令已过期或尚未领取，请先上报最新遥测',409)
  relay=b.get('relay_on');success=b.get('success')
  if type(relay) is not bool or type(success) is not bool:raise Error('回执缺少设备执行状态')
  confirmed=success and relay==bool(c['desired']);state='confirmed' if confirmed else 'rejected'
  self.db.execute('UPDATE commands SET state=?,finished=?,detail=? WHERE id=?',(state,clock(),'设备执行回执已确认' if confirmed else '设备未达到目标状态',c['id']))
  self.db.execute('UPDATE devices SET relay_on=? WHERE id=?',(int(relay),c['device_id']));self.audit('gateway:'+g['name'],'command_ack',c['device_id'],jdump({'command_id':c['id'],'state':state}));self.db.commit();return {'state':state}
 @atomic
 def dispatch(self,method,path,user,b,params):
  with self.lock:
   sid=params.get('space_id') or b.get('space_id') or 'space-demo'
   if method=='GET':
    if path=='/api/preferences':
     row=self.one('SELECT value FROM settings WHERE key=?',('ui:'+str(user.get('id','demo')),));return {'preferences':json.loads(row['value']) if row else None}
    if path=='/api/spaces':return {'spaces':self.spaces()}
    if path=='/api/snapshot':return self.snapshot(sid)
    if path=='/api/reports':return self.reports(sid,params.get('days',7))
    if path=='/api/cameras':return {'cameras':self.rows('SELECT * FROM cameras WHERE space_id=?',(sid,))}
    if path=='/api/commands':return {'commands':self.rows('SELECT c.*,d.name device_name FROM commands c JOIN devices d ON d.id=c.device_id WHERE c.space_id=? ORDER BY created DESC LIMIT 200',(sid,))}
    if path=='/api/alerts':return {'alerts':self.rows('SELECT a.*,d.name device_name FROM alerts a LEFT JOIN devices d ON d.id=a.device_id WHERE a.space_id=? ORDER BY a.created DESC LIMIT 200',(sid,))}
    if path=='/api/schedules':return {'schedules':self.rows('SELECT s.*,d.name device_name FROM schedules s JOIN devices d ON d.id=s.device_id WHERE s.space_id=?',(sid,))}
    if path=='/api/audit':return {'audit':self.rows('SELECT * FROM audit ORDER BY id DESC LIMIT 300')}
    if path=='/api/system':return {'version':VERSION,'heartbeat':self.last_tick,'last_error':self.last_error,'data_path':str(self.directory),'owner_configured':bool(self.db.execute('SELECT 1 FROM users').fetchone()),'users':self.rows('SELECT id,username,role FROM users') if user['role']=='admin' else [],'gateways':self.rows('SELECT id,name,space_id,last_seen,enabled FROM gateways') if user['role']=='admin' else [],'retention':{'raw_hours':6,'hourly_days':400}}
   if method=='POST':
    if path=='/api/preferences':
     import re
     if b.get('language') not in ('zh','en') or b.get('mode') not in ('light','dark','auto') or not re.fullmatch(r'#[0-9a-fA-F]{6}',str(b.get('primary',''))) or b.get('radius') not in ('0','0.25','0.5','0.75','1') or type(b.get('border')) is not bool:raise Error('界面偏好格式无效')
     p={k:b[k] for k in ('language','mode','primary','radius','border')};self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ui:'+str(user.get('id','demo')),jdump(p)));self.db.commit();return {'ok':True}
    if path=='/api/command':return self.command(user,b)
    if path=='/api/spaces':return self.save_space(user,b)
    if path=='/api/devices':return self.save_device(user,b)
    if path=='/api/tariff':return self.save_tariff(user,b)
    if path=='/api/cameras':return self.save_camera(user,b)
    if path=='/api/schedules':return self.save_schedule(user,b)
    if path=='/api/setup':return self.setup(user,b)
    if path=='/api/users':return self.save_user(user,b)
    if path=='/api/gateways':return self.create_gateway(user,b)
    if path=='/api/schedule_toggle':
     self.require(user,('admin','demo'));r=self.one('SELECT * FROM schedules WHERE id=?',(text(b.get('id')),))
     if not r:raise Error('计划不存在',404)
     self.db.execute('UPDATE schedules SET enabled=? WHERE id=?',(int(bool(b.get('enabled'))),r['id']));self.audit(user['username'],'toggle_schedule',r['id'],str(bool(b.get('enabled'))));self.db.commit();return {'ok':True}
    if path=='/api/alert_ack':
     self.require(user);row=self.one('SELECT * FROM alerts WHERE id=?',(text(b.get('id')),))
     if not row:raise Error('告警不存在',404)
     if row['state']=='active':self.db.execute("UPDATE alerts SET state='acknowledged',ack_by=? WHERE id=?",(user['username'],row['id']));self.audit(user['username'],'acknowledge_alert',row['id'],'已确认收到，未标记故障已排除');self.db.commit()
     return {'ok':True}
  raise Error('接口不存在',404)
 def close(self):
  self.stop.set()
  with self.lock:self.db.close()

_LegacyEnergy=Energy
from v1 import V1Mixin
class Energy(V1Mixin,_LegacyEnergy):
 pass
