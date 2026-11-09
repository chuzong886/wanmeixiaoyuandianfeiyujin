"""Configurable read-only HTTP devices, including the existing encrypted campus protocol."""
from __future__ import annotations
import hashlib,http.client,json,math,os,re,subprocess,threading,time,urllib.parse,uuid
from pathlib import Path
from energy import Error,clock,jdump,number,local
from protected import transform

SCHEMA='''
CREATE TABLE IF NOT EXISTS api_devices(device_id TEXT PRIMARY KEY,config BLOB NOT NULL,series TEXT NOT NULL,next_poll REAL NOT NULL DEFAULT 0,last_attempt REAL NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT '未查询',rooms TEXT NOT NULL DEFAULT '[]',forecast TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS api_readings(id INTEGER PRIMARY KEY AUTOINCREMENT,device_id TEXT NOT NULL,series TEXT NOT NULL,ts REAL NOT NULL,balance REAL,cumulative REAL,power REAL,voltage REAL,quality TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS api_history ON api_readings(device_id,series,ts);
CREATE TABLE IF NOT EXISTS api_attempts(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,ts REAL NOT NULL,state TEXT NOT NULL,message TEXT NOT NULL);
'''
FIELDS=('balance_kwh','cumulative_kwh','power_w','voltage')
def url(value):
 value=str(value).strip();u=urllib.parse.urlsplit(value)
 try:port=u.port
 except ValueError:raise Error('请求端口无效')
 if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or u.fragment or len(value)>2000 or any(c in value for c in '\r\n\\'):raise Error('请填写不含用户名密码的 HTTP/HTTPS 请求网址')
 if u.scheme=='http' and u.hostname not in ('127.0.0.1','localhost','::1'):raise Error('第三方平台请使用 HTTPS；本机测试可使用 HTTP')
 return value
def path_value(data,path):
 # Property/index traversal only. No code evaluation or executable expressions.
 for part in path.split('.'):
  if isinstance(data,list) and part.isdigit():data=data[int(part)]
  elif isinstance(data,dict) and part in data:data=data[part]
  else:raise Error('返回数据中缺少配置的字段：'+path)
 return data
def validate(raw):
 if not isinstance(raw,dict):raise Error('请配置第三方 API 参数')
 c=dict(raw);c['url']=url(c.get('url',''));c['adapter']=c.get('adapter','generic');c['method']=c.get('method','POST');c['encoding']=c.get('encoding','json')
 if c['adapter'] not in ('generic','wanxiao') or c['method'] not in ('GET','POST') or c['encoding'] not in ('json','form'):raise Error('接口协议、请求方式或编码无效')
 for key in ('headers','body','mapping'):
  if not isinstance(c.get(key,{}),dict):raise Error('请求头、参数及字段映射必须是 JSON 对象')
  c[key]=c.get(key,{})
 for k,v in c['headers'].items():
  if not re.fullmatch(r'[A-Za-z0-9-]{1,80}',k) or k.lower() in ('host','content-length','transfer-encoding','connection') or not isinstance(v,str) or '\r' in v or '\n' in v:raise Error('请求头格式无效或包含不允许覆盖的头')
 c['interval']=int(number(c.get('interval',3600),3600 if c['adapter']=='wanxiao' else 60,86400));c['enabled']=bool(c.get('enabled',False))
 c['warning_kwh']=number(c.get('warning_kwh',20));c['critical_kwh']=number(c.get('critical_kwh',3));c['reserve_kwh']=number(c.get('reserve_kwh',3));c['forecast_notice_hours']=number(c.get('forecast_notice_hours',12),0,168)
 if c['critical_kwh']>c['warning_kwh']:raise Error('紧急电量不能高于预警电量')
 c['max_queries']=int(number(c.get('max_queries',8),1,24));c['window_start']=str(c.get('window_start','08:00'));c['window_end']=str(c.get('window_end','22:00'))
 if not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',c[k]) for k in ('window_start','window_end')) or c['window_start']>=c['window_end']:raise Error('查询窗口应为同一天内的有效起止时间')
 if c['adapter']=='wanxiao':
  for k in ('school_code','account'):
   c[k]=str(c.get(k,'')).strip()
   if not c[k] or len(c[k])>200:raise Error('请填写学校编号和查询账户')
  for k,default in [('roomverify',''),('business_type','0'),('command','JBSWaterElecService')]:c[k]=str(c.get(k,default)).strip()
  c['method']='POST';c['encoding']='form'
 elif not any(c['mapping'].get(k) for k in FIELDS[:3]):raise Error('至少映射剩余电量、累计电量或瞬时功率中的一个字段')
 for k,v in c['mapping'].items():
  if k not in FIELDS or not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff.-]{0,160}',v):raise Error('字段路径格式无效，请使用 data.balance 或 data.0.power 形式')
 if len(jdump(c).encode())>64000:raise Error('接口配置过大')
 return c

class ThirdParty:
 def __init__(self,app,runtime=None):
  self.app=app;self.runtime=Path(runtime or Path(__file__).resolve().parents[1]/'api-runtime');self.inflight=set();self.pool=threading.BoundedSemaphore(2)
  app.db.executescript(SCHEMA)
  if 'resolved_room' not in {r['name'] for r in app.rows('PRAGMA table_info(api_devices)')}:app.db.execute("ALTER TABLE api_devices ADD COLUMN resolved_room TEXT NOT NULL DEFAULT ''")
  app.db.execute("UPDATE api_attempts SET state='unknown',message='服务重启，查询结果未确认' WHERE state='started'");app.db.commit()
 def adapter(self,action,**body):
  java=self.runtime/'runtime/bin/java.exe';jar=self.runtime/'adapters.jar'
  if not java.is_file() or not jar.is_file():raise Error('缺少第三方协议适配器，请使用完整运行包',503)
  try:
   p=subprocess.run([str(java),'-cp',str(jar)+os.pathsep+str(self.runtime/'json.jar'),'campus.local.ApiAdapter'],input=jdump({'action':action,**body}).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
   result=json.loads(p.stdout)
  except (OSError,ValueError,subprocess.SubprocessError):raise Error('第三方协议适配处理失败',502)
  if result.get('error'):raise Error(result['error'],502)
  return result
 def config(self,did):
  row=self.app.one('SELECT * FROM api_devices WHERE device_id=?',(did,))
  if not row:raise Error('第三方接口未配置',404)
  return json.loads(transform(row['config'],False))
 def save(self,did,config):
  c=validate(config);identity={k:c.get(k) for k in ('url','adapter','method','encoding','body','mapping','school_code','account','roomverify','business_type','command')};series=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();old=self.app.one('SELECT series FROM api_devices WHERE device_id=?',(did,))
  if did in self.inflight:raise Error('正在查询此设备，请等待完成再修改配置',409)
  if old and old['series']!=series and self.app.one('SELECT id FROM api_readings WHERE device_id=? LIMIT 1',(did,)):raise Error('此电表已有历史，请新建设备以更换请求身份或返回映射；原记录继续保留',409)
  blob=transform(jdump(c).encode(),True)
  self.app.db.execute('INSERT INTO api_devices(device_id,config,series,next_poll) VALUES(?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET config=excluded.config,series=excluded.series,next_poll=excluded.next_poll',(did,blob,series,clock()+c['interval']))
  if old and old['series']!=series:
   self.app.db.execute("UPDATE api_devices SET forecast='{}',rooms='[]',status='接口身份已变更，等待新读数',failures=0 WHERE device_id=?",(did,));self.app.db.execute('UPDATE devices SET last_seen=0,power_w=0,total_kwh=0 WHERE id=?',(did,));self.app.db.execute('DELETE FROM hourly WHERE device_id=?',(did,));self.app.db.execute('DELETE FROM samples WHERE device_id=?',(did,))
  self.app.report_cache.clear()
 def public(self,d):
  row=self.app.one('SELECT * FROM api_devices WHERE device_id=?',(d['id'],))
  if not row:return
  try:c=self.config(d['id'])
  except (Error,ValueError):
   d.update(online=False,has_power=False,has_voltage=False,current_a=None,relay_on=None,controllable=False,api={'status':'配置无法解密，请管理员编辑设备并重新填写接口信息','configuration_error':True,'enabled':False,'rooms':[],'reading':None,'forecast':{},'last_attempt':row['last_attempt'],'next_poll':None});return
  last=self.app.one('SELECT * FROM api_readings WHERE device_id=? AND series=? ORDER BY ts DESC LIMIT 1',(d['id'],row['series']))
  d['api']={'status':row['status'],'interval':c['interval'],'enabled':c['enabled'],'last_attempt':row['last_attempt'],'next_poll':row['next_poll'] if c['enabled'] else None,'rooms':json.loads(row['rooms']),'warning_kwh':c['warning_kwh'],'critical_kwh':c['critical_kwh'],'reading':{k:last[k] for k in ('ts','balance','cumulative','power','quality')} if last else None,'forecast':json.loads(row['forecast'])}
  d['api']['failures']=row['failures']
  d['online']=bool(last and clock()-last['ts']<=max(180,c['interval']*2) and row['failures']==0);d['has_power']=bool(last and last['power'] is not None and clock()-last['ts']<=10);d['has_voltage']=bool(last and last['voltage'] is not None);d['current_a']=None;d['relay_on']=None;d['controllable']=False
  f=d['api']['forecast'].get('forecast',{})
  if f.get('origin') and clock()-f['origin']>72*3600:f['available']=False;f['data_state']='lost'
  guard=d['api']['forecast'].get('analysis',{}).get('guard_rate',0)
  if last and guard>0 and last['balance'] is not None:f['remaining_hours']=max(0,(last['balance']-c['reserve_kwh'])/guard-(clock()-last['ts'])/3600);f['empty_at']=clock()+f['remaining_hours']*3600;f['beyond_horizon']=f['remaining_hours']>72
  if f.get('empty_at'):f['remaining_hours']=max(0,(f['empty_at']-clock())/3600)
 def transport(self,c,payload):
  u=urllib.parse.urlsplit(c['url']);headers=dict(c['headers']);headers.setdefault('Accept','application/json')
  if c['adapter']=='wanxiao':
   headers.setdefault('Origin','https://xqh5.17wanxiao.com');headers.setdefault('Referer','https://xqh5.17wanxiao.com/userwaterelecmini/index.html');headers.setdefault('X-Requested-With','com.newcapec.mobile.ncp')
  target=(u.path or '/')+('?' +u.query if u.query else '');body=None
  if c['method']=='GET':
   query=urllib.parse.urlencode(payload,doseq=True);target+=('&' if u.query else '?')+query if query else ''
  elif c['encoding']=='form':body=urllib.parse.urlencode(payload,doseq=True).encode();headers['Content-Type']='application/x-www-form-urlencoded; charset=utf-8'
  else:body=jdump(payload).encode();headers['Content-Type']='application/json; charset=utf-8'
  conn=(http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection)(u.hostname,u.port,timeout=25)
  try:
   conn.request(c['method'],target,body=body,headers=headers);r=conn.getresponse();raw=r.read(2*1024*1024+1)
   if not 200<=r.status<300:raise Error('第三方平台返回 HTTP '+str(r.status)+'；未跟随重定向，也未保存为有效读数',502)
   if len(raw)>2*1024*1024:raise Error('第三方响应过大',502)
   try:value=json.loads(raw)
   except (ValueError,UnicodeError):raise Error('第三方响应不是有效 JSON',502)
   if not isinstance(value,(dict,list)):raise Error('第三方响应应为 JSON 对象或数组',502)
   return value
  except (OSError,http.client.HTTPException):raise Error('第三方请求超时或连接失败；未保存读数，请稍后刷新状态确认',502)
  finally:conn.close()
 def normalize(self,c,response):
  if c['adapter']=='wanxiao':
   value=self.adapter('decode',config=c,response=response)
   if value.get('selection_required'):return {'rooms':value.get('rooms',[]),'selection_required':True}
   return {'balance_kwh':number(value['balance'],-1e12)*number(c.get('scale',1),.000001,1000000),'rooms':value.get('rooms',[]),'resolved_room':value.get('roomverify','')}
  success=c.get('success_path','').strip()
  if success and str(path_value(response,success)).lower()!=str(c.get('success_value','0')).lower():raise Error('第三方业务状态未通过，未保存为有效读数',502)
  out={}
  for k,path in c['mapping'].items():
   if path:
    raw=path_value(response,path)
    if raw is None or type(raw) is bool or (isinstance(raw,str) and not raw.strip()):raise Error('第三方数值为空或无效，未保存读数',502)
    out[k]=number(raw,-1e12 if k=='balance_kwh' else 0,1e12)*(1 if k=='voltage' else number(c.get('scale',1),.000001,1000000))
  if not any(k in out for k in FIELDS[:3]):raise Error('未找到有效的电量或功率字段',502)
  return out
 def query(self,user,did,automatic=False):
  a=self.app
  with a.lock:
   if not automatic:a.require(user,('admin',))
   d=a.device(did)
   if d['source']!='third_party':raise Error('此设备未使用第三方 API')
   if did in self.inflight:raise Error('此设备已有查询执行中，请等待结果',409)
   c=self.config(did);row=a.one('SELECT * FROM api_devices WHERE device_id=?',(did,));now=clock()
   if now-row['last_attempt']<60:raise Error('请至少间隔 60 秒查询，避免频繁访问第三方平台',429)
   self.inflight.add(did);attempt=uuid.uuid4().hex;a.db.execute('INSERT INTO api_attempts VALUES(?,?,?,?,?)',(attempt,did,now,'started','正在读取第三方接口'));a.db.execute('UPDATE api_devices SET last_attempt=?,next_poll=? WHERE device_id=?',(now,now+c['interval'],did));a.db.commit()
  try:
   payload=self.adapter('prepare',config=c) if c['adapter']=='wanxiao' else c['body'];response=self.transport(c,payload);values=self.normalize(c,response)
   with a.lock:
    a.db.execute('UPDATE api_devices SET rooms=? WHERE device_id=?',(jdump(values.get('rooms',[])),did))
    if values.get('selection_required'):raise Error('发现多个宿舍或项目，请编辑设备并选择对应房间标识',409)
    room=values.get('resolved_room','')
    if room and row.get('resolved_room') and row['resolved_room']!=room:raise Error('实际电表身份已变化，请建立替换设备',409)
    if room:a.db.execute('UPDATE api_devices SET resolved_room=? WHERE device_id=?',(room,did))
    a.db.execute('SAVEPOINT api_accept')
    try:self.accept(d,row,c,values,clock());a.db.execute('RELEASE api_accept')
    except BaseException:a.db.execute('ROLLBACK TO api_accept');a.db.execute('RELEASE api_accept');raise
    a.db.execute("UPDATE api_attempts SET state='ok',message='有效读数已保存' WHERE id=?",(attempt,));a.db.execute("UPDATE api_devices SET failures=0,status='查询成功' WHERE device_id=?",(did,));a.audit('自动查询' if automatic else user['username'],'api_query',did,'第三方有效读数已保存；未记录网址、参数或凭据');a.db.commit()
   try:self.refresh_forecast(did)
   except Error:
    with a.lock:a.db.execute("UPDATE api_devices SET forecast=? WHERE device_id=?",(jdump({'error':'读数已保存，预测暂未更新'}),did));a.db.commit()
   return {'ok':True,'message':'接口查询成功，读数已进入当前空间的设备、历史和告警'}
  except Exception as e:
   message=str(e) if isinstance(e,Error) else '第三方接口处理失败，未确认有效读数'
   with a.lock:
    a.db.execute("UPDATE api_attempts SET state='failed',message=? WHERE id=?",(message,attempt));a.db.execute('UPDATE api_devices SET failures=failures+1,status=?,next_poll=? WHERE device_id=?',(message,clock()+min(86400,c['interval']*2**min(row['failures'],3)),did));a.db.commit()
   if isinstance(e,Error):raise
   raise Error(message,502)
  finally:
   with a.lock:self.inflight.discard(did)
 def accept(self,d,row,c,v,now):
  a=self.app;did=d['id'];previous=a.one('SELECT * FROM api_readings WHERE device_id=? AND series=? ORDER BY ts DESC LIMIT 1',(did,row['series']));quality='first';energy=None;anchor=previous
  # Keep the original debit anchor across cached equal balances; never call a plateau zero use.
  if 'cumulative_kwh' in v and previous and previous['cumulative'] is not None:energy=v['cumulative_kwh']-previous['cumulative']
  elif 'balance_kwh' in v:
   if previous and previous['balance'] is not None:
    changed=a.one('SELECT MAX(ts) ts FROM api_readings WHERE device_id=? AND series=? AND balance!=?',(did,row['series'],previous['balance']))
    anchor=a.one('SELECT * FROM api_readings WHERE device_id=? AND series=? AND balance=? AND ts>? ORDER BY ts LIMIT 1',(did,row['series'],previous['balance'],changed['ts'] or 0))
   if previous and previous['balance']==v['balance_kwh']:quality='unchanged';anchor=None
   elif anchor and anchor['balance'] is not None:energy=anchor['balance']-v['balance_kwh']
  elif 'power_w' in v and previous and previous['power'] is not None and now-previous['ts']<=max(180,c['interval']*1.5):energy=(v['power_w']+previous['power'])/2*(now-previous['ts'])/3600000
  if energy is not None and anchor:
   minimum=1
   elapsed=now-anchor['ts'];quality='recharge_or_reset' if energy<0 else 'short_interval' if elapsed<minimum else 'gap' if elapsed>48*3600 else 'allocated_estimate'
   if energy>=0 and minimum<=elapsed<=48*3600:
    begin=anchor['ts']
    while begin<now:
     hour=int(begin//3600)*3600;end=min(now,hour+3600);seconds=end-begin;wh=energy*1000*seconds/elapsed
     a.db.execute('INSERT INTO hourly VALUES(?,?,?,?,0) ON CONFLICT(device_id,hour) DO UPDATE SET energy_wh=energy_wh+excluded.energy_wh,coverage_s=MIN(3600,coverage_s+excluded.coverage_s)',(did,hour,wh,seconds));begin=end
    a.db.execute('UPDATE devices SET total_kwh=total_kwh+? WHERE id=?',(energy,did))
  if previous and v.get('balance_kwh') is not None and previous['balance'] is not None and v['balance_kwh']>previous['balance']:
   if a.one("SELECT name FROM sqlite_master WHERE name='recharges'"):a.db.execute('INSERT INTO recharges(device_id,ts,amount,kind) VALUES(?,?,?,?)',(did,now,v['balance_kwh']-previous['balance'],'possible_recharge'))
  a.db.execute('INSERT INTO api_readings(device_id,series,ts,balance,cumulative,power,voltage,quality) VALUES(?,?,?,?,?,?,?,?)',(did,row['series'],now,v.get('balance_kwh'),v.get('cumulative_kwh'),v.get('power_w'),v.get('voltage'),quality))
  a.db.execute('UPDATE devices SET last_seen=?,last_sample=?,power_w=?,voltage=? WHERE id=?',(now,now,v.get('power_w',0),v.get('voltage',0),did));a.report_cache.clear()
 def refresh_forecast(self,did):
  a=self.app
  with a.lock:
   row=a.one('SELECT * FROM api_devices WHERE device_id=?',(did,));c=self.config(did);rows=a.rows('SELECT ts,balance,quality FROM api_readings WHERE device_id=? AND series=? AND balance IS NOT NULL ORDER BY ts DESC LIMIT 1000',(did,row['series']))
  if not rows:return
  history=[{**r,'balance_raw':str(r['balance'])} for r in reversed(rows)];value=self.adapter('forecast',history=history,options={k:c[k] for k in ('warning_kwh','critical_kwh','reserve_kwh','forecast_notice_hours')})
  with a.lock:
   a.db.execute('UPDATE api_devices SET forecast=? WHERE device_id=? AND series=?',(jdump(value),did,row['series']));a.db.commit()
 def schedule(self):
  a=self.app
  with a.lock:
   due=a.rows("SELECT p.* FROM api_devices p JOIN devices d ON d.id=p.device_id WHERE d.enabled=1 AND p.next_poll<=?",(clock(),))
   for row in due:
    did=row['device_id']
    if did in self.inflight:continue
    try:c=self.config(did)
    except Error:continue
    if not c['enabled']:continue
    if c['adapter']=='wanxiao':
     now=local();day=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp();count=a.one('SELECT COUNT(*) n FROM api_attempts WHERE device_id=? AND ts>=?',(did,day))['n']
     if not c['window_start']<=now.strftime('%H:%M')<=c['window_end'] or count>=c['max_queries']:continue
    if not self.pool.acquire(False):break
    a.db.execute('UPDATE api_devices SET next_poll=? WHERE device_id=?',(clock()+c['interval'],did));a.db.commit()
    def work(device=did):
     try:
      self.query({'username':'自动查询','role':'admin'},device,True)
     except Exception:pass # Persisted attempt/status already contains the safe diagnostic.
     finally:self.pool.release()
    threading.Thread(target=work,daemon=True).start()
 def history(self,user,did):
  d=self.app.device(did);row=self.app.one('SELECT * FROM api_devices WHERE device_id=?',(did,))
  if not row:raise Error('设备不是第三方 API 电表',404)
  return {'device':{k:d[k] for k in ('id','name','area')},'readings':self.app.rows('SELECT ts,balance,cumulative,power,quality,series FROM api_readings WHERE device_id=? ORDER BY ts DESC LIMIT 1000',(did,)),'attempts':self.app.rows('SELECT ts,state,message FROM api_attempts WHERE device_id=? ORDER BY ts DESC LIMIT 100',(did,)),'forecast':json.loads(row['forecast'])}
