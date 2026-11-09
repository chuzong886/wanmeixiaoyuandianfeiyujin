"""Local synthetic meter API + gateway laboratory. Never talks to physical hardware."""
from __future__ import annotations
import argparse,base64,http.client,json,math,mimetypes,secrets,sqlite3,sys,threading,time,urllib.parse,uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from energy import Error,number,text,jdump
from protected import transform
from simulator_camera import CameraFeed,photo_index

VERSION='1.0.1'
def seal(value):return base64.b64encode(transform(jdump(value).encode(),True)).decode()
def unseal(value):return json.loads(transform(base64.b64decode(value),False))
def local_target(value):
 try:u=urllib.parse.urlsplit(str(value).strip());port=u.port
 except ValueError:raise Error('天枢地址格式无效')
 if u.scheme!='http' or u.hostname!='127.0.0.1' or not port or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:raise Error('只允许 http://127.0.0.1:端口 的本机天枢服务')
 return f'http://127.0.0.1:{port}'

class Simulator:
 def __init__(self,directory):
  self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.stop=threading.Event();self.heartbeat=0;self.last_error=None;self.last_tick=time.time();self.gateway_status={};self.sessions={}
  self.db=sqlite3.connect(self.directory/'simulator.sqlite',check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.executescript('PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS meters(id TEXT PRIMARY KEY,config TEXT NOT NULL); CREATE TABLE IF NOT EXISTS gateways(id TEXT PRIMARY KEY,config TEXT NOT NULL); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL,kind TEXT,message TEXT); CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,gateway_id TEXT,meter_id TEXT,desired INTEGER,expires REAL,due REAL,mode TEXT,state TEXT,relay INTEGER,error TEXT);')
  if not self.db.execute("SELECT 1 FROM settings WHERE key='api-key'").fetchone():self.db.execute("INSERT INTO settings VALUES('api-key',?)",(seal(secrets.token_urlsafe(32)),))
  # A claimed command is never executed again after a restart. Lost acknowledgements remain unknown.
  self.db.execute("UPDATE commands SET state='unknown',error='程序重启，未确认执行结果；不重放指令' WHERE state IN ('received','executed')");self.db.commit()
  if not self.db.execute('SELECT 1 FROM meters').fetchone():self.save_meter({'name':'模拟电表 01','balance_kwh':50,'cumulative_kwh':100,'nominal_w':1000,'mode':'full','controllable':True})
 def rows(self,sql,args=()):return [dict(r) for r in self.db.execute(sql,args).fetchall()]
 def meter(self,mid):
  r=self.db.execute('SELECT config FROM meters WHERE id=?',(mid,)).fetchone()
  if not r:raise Error('模拟电表不存在',404)
  return json.loads(r[0])
 def write_meter(self,m):self.db.execute('INSERT OR REPLACE INTO meters VALUES(?,?)',(m['id'],jdump(m)))
 def event(self,kind,message):self.db.execute('INSERT INTO events(ts,kind,message) VALUES(?,?,?)',(time.time(),kind,message));self.db.execute('DELETE FROM events WHERE id<(SELECT COALESCE(MAX(id),0)-999 FROM events)')
 def gateway(self,gid,secret=False):
  row=self.db.execute('SELECT config FROM gateways WHERE id=?',(gid,)).fetchone()
  if not row:raise Error('模拟网关不存在',404)
  g=json.loads(row[0]);g['has_token']=bool(g.get('token'))
  if secret:g['token']=unseal(g['token']) if g.get('token') else ''
  else:g.pop('token',None)
  return g
 def snapshot(self):
  with self.lock:return {'simulation':True,'version':VERSION,'heartbeat':self.heartbeat,'meters':[json.loads(r['config']) for r in self.rows('SELECT config FROM meters')],'gateways':[{**self.gateway(r['id']),'status':self.gateway_status.get(r['id'],{'state':'stopped','message':'尚未连接'})} for r in self.rows('SELECT id FROM gateways')],'events':self.rows('SELECT * FROM events ORDER BY id DESC LIMIT 100'),'commands':self.rows('SELECT id,gateway_id,meter_id,desired,expires,mode,state,error FROM commands ORDER BY rowid DESC LIMIT 100')}
 def save_meter(self,b):
  with self.lock:
   old=self.meter(b['id']) if b.get('id') else {};mid=old.get('id') or uuid.uuid4().hex
   if not old and self.db.execute('SELECT COUNT(*) FROM meters').fetchone()[0]>=100:raise Error('最多100台模拟设备')
   mode=b.get('mode','full');profile=b.get('profile','wave');fault=b.get('fault','normal')
   if mode not in ('balance','cumulative','full') or profile not in ('constant','wave','spike') or fault not in ('normal','http500','business','malformed','timeout','frozen'):raise Error('模拟模式无效')
   # Editing ordinary parameters must not silently change accumulated history or recharge a meter.
   m={'id':mid,'name':text(b.get('name'),80),'mode':mode,'profile':profile,'fault':fault,'nominal_w':number(b.get('nominal_w',1000),0,1e7),'voltage':number(b.get('voltage',220),1,1000),'controllable':bool(b.get('controllable',False)),'online':bool(b.get('online',True)),'relay_on':old.get('relay_on',True),'balance_kwh':old.get('balance_kwh',number(b.get('balance_kwh',50),-1e7,1e7)),'cumulative_kwh':old.get('cumulative_kwh',number(b.get('cumulative_kwh',0),0,1e10)),'power_w':old.get('power_w',0),'sequence':old.get('sequence',0),'created':old.get('created',time.time()),'updated_at':old.get('updated_at',time.time())}
   if fault=='frozen':m['frozen']=old.get('frozen') if old.get('fault')=='frozen' else self.reading(m)
   self.write_meter(m);self.event('meter','保存模拟设备 '+m['name']);self.db.commit();return m
 def change_meter(self,b):
  with self.lock:
   m=self.meter(text(b.get('id')));action=b.get('action')
   if action=='recharge':
    amount=number(b.get('amount'),.001,1e7);m['balance_kwh']+=amount;detail='模拟充值 '+str(amount)+' kWh'
   elif action=='balance':m['balance_kwh']=number(b.get('amount'),-1e7,1e7);detail='设置模拟余额'
   elif action=='reset':
    if b.get('confirmation')!='清零模拟电表':raise Error('请输入清零模拟电表')
    m['cumulative_kwh']=0;detail='模拟电表累计清零'
   elif action=='relay':
    if not m['controllable']:raise Error('只读模拟电表没有开关能力')
    if type(b.get('relay_on')) is not bool:raise Error('开关状态无效')
    m['relay_on']=b['relay_on']
    if not m['relay_on']:m['power_w']=0
    detail='模拟现场通断'
   else:raise Error('操作不存在')
   self.write_meter(m);self.event('meter',m['name']+'：'+detail);self.db.commit();return m
 def reading(self,m):
  d={'meter_id':m['id'],'name':m['name'],'simulation':True,'timestamp':m['updated_at']}
  if m['mode'] in ('balance','full'):d['balance_kwh']=round(m['balance_kwh'],6)
  if m['mode'] in ('cumulative','full'):d['cumulative_kwh']=round(m['cumulative_kwh'],6)
  if m['mode']=='full':d.update(power_w=round(m['power_w'],3) if m['online'] else None,voltage=m['voltage'] if m['online'] else None)
  return d
 def api_read(self,mid,key):
  with self.lock:
   expected=unseal(self.db.execute("SELECT value FROM settings WHERE key='api-key'").fetchone()[0])
   if not secrets.compare_digest(str(key),'Bearer '+expected):raise Error('模拟 API 认证失败',401)
   m=self.meter(mid);fault=m['fault'];data=m.get('frozen') if fault=='frozen' else self.reading(m)
   self.event('query','模拟 API 查询 '+m['name']+' · '+fault);self.db.commit()
  return fault,{'code':0,'simulation':True,'data':data}
 def api_key(self):
  with self.lock:return unseal(self.db.execute("SELECT value FROM settings WHERE key='api-key'").fetchone()[0])
 def save_gateway(self,b):
  with self.lock:
   old=self.gateway(b['id'],True) if b.get('id') else {};gid=old.get('id') or uuid.uuid4().hex;target=local_target(b.get('target',''));token=str(b.get('token') or old.get('token','')).strip()
   if len(token)>4096 or any(c in token for c in '\r\n'):raise Error('网关令牌格式无效')
   if old.get('target') and old['target']!=target and not b.get('token'):raise Error('更换目标地址必须重新填写令牌')
   enabled=bool(b.get('enabled'));confirmed=b.get('simulation_only') is True
   if enabled and (not token or not confirmed):raise Error('启用前请填写令牌并确认是专用模拟网关')
   mode=b.get('ack_mode','success')
   if mode not in ('success','failure','timeout','mismatch'):raise Error('回执模式无效')
   maps=b.get('mappings',[])
   if not isinstance(maps,list) or len(maps)>100:raise Error('设备映射最多100项')
   normalized=[];seen=set()
   for item in maps:
    mid=text(item.get('meter_id'));did=text(item.get('device_id'),100);self.meter(mid)
    if did in seen or any(x['meter_id']==mid for x in normalized):raise Error('同一网关内设备不能重复映射')
    if any(ch.isspace() for ch in did):raise Error('设备 ID 不能包含空白')
    seen.add(did);normalized.append({'meter_id':mid,'device_id':did})
   if enabled and not normalized:raise Error('启用前请添加设备映射')
   if not old and self.db.execute('SELECT COUNT(*) FROM gateways').fetchone()[0]>=8:raise Error('最多8个模拟网关')
   # Prevent two gateway loops from controlling the same local synthetic meter.
   for other in self.rows('SELECT id FROM gateways WHERE id!=?',(gid,)):
    g=self.gateway(other['id'])
    if enabled and g['enabled'] and any(x['meter_id'] in {v['meter_id'] for v in normalized} for x in g['mappings']):raise Error('一个网关可连接多台设备；但这台模拟设备已绑定到另一个已启用网关，请先停用原网关或移除原映射')
   g={'id':gid,'name':text(b.get('name'),80),'target':target,'token':seal(token),'enabled':enabled,'simulation_only':confirmed,'ack_mode':mode,'ack_delay':number(b.get('ack_delay',1),0,25),'mappings':normalized};self.db.execute('INSERT OR REPLACE INTO gateways VALUES(?,?)',(gid,jdump(g)));self.event('gateway','保存模拟网关 '+g['name']+' · '+('启用' if enabled else '停用'));self.db.commit();return self.gateway(gid)
 def tick(self,now=None):
  now=time.time() if now is None else now
  with self.lock:
   dt=max(0,min(5,now-self.last_tick));self.last_tick=now
   for r in self.rows('SELECT config FROM meters'):
    m=json.loads(r['config']);phase=now-m['created'];factor=1 if m['profile']=='constant' else 1.8 if m['profile']=='spike' and int(phase)%60<15 else .7+.2*math.sin(phase/15)
    m['power_w']=max(0,m['nominal_w']*factor) if m['relay_on'] else 0;used=m['power_w']*dt/3600000;m['balance_kwh']-=used;m['cumulative_kwh']+=used
    if m['online']:m['updated_at']=now
    self.write_meter(m)
   self.heartbeat=now;self.db.commit()
 def request(self,g,path,body=None):
  u=urllib.parse.urlsplit(local_target(g['target']));conn=http.client.HTTPConnection('127.0.0.1',u.port,timeout=2);headers={'Authorization':'Bearer '+g['token'],'Content-Type':'application/json'}
  try:
   conn.request('POST' if body is not None else 'GET',path,jdump(body).encode() if body is not None else None,headers);r=conn.getresponse();raw=r.read(262145)
   if r.status!=200 or len(raw)>262144:raise Error('天枢返回 HTTP '+str(r.status))
   value=json.loads(raw)
   if not isinstance(value,dict):raise Error('天枢响应格式无效')
   return value
  finally:conn.close()
 def claim(self,g,commands):
  with self.lock:
   by_device={m['device_id']:m['meter_id'] for m in g['mappings']}
   for c in commands[:100]:
    cid=text(c.get('id'),100)
    if self.db.execute('SELECT 1 FROM commands WHERE id=?',(cid,)).fetchone():continue
    mid=by_device.get(c.get('device_id'),'');desired=c.get('desired');expires=number(c.get('expires'),0,1e12)
    if desired not in (0,1):raise Error('目标指令无效')
    state='received' if mid else 'failed';error='' if mid else '未映射设备，拒绝执行'
    self.db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,g['id'],mid,int(desired),expires,time.time()+g['ack_delay'],g['ack_mode'],state,None,error));self.event('command','领取模拟指令 '+cid+' · '+state)
   self.db.commit()
 def execute_due(self,g):
  now=time.time()
  with self.lock:
   pending=self.rows("SELECT * FROM commands WHERE gateway_id=? AND state IN ('received','executed','failed')",(g['id'],))
   for c in pending:
    if c['expires']<=now:self.db.execute("UPDATE commands SET state='expired',error='回执截止时间已过' WHERE id=?",(c['id'],));continue
    if c['due']>now:continue
    if c['state']=='received':
     m=self.meter(c['meter_id'])
     if c['mode']=='timeout':self.db.execute("UPDATE commands SET state='noack',error='模拟无回执；未执行通断' WHERE id=?",(c['id'],));continue
     success=m['controllable'] and m['online'] and c['mode']=='success'
     if success:
      m['relay_on']=bool(c['desired'])
      if not m['relay_on']:m['power_w']=0
      self.write_meter(m)
     relay=int(not c['desired']) if c['mode']=='mismatch' else int(m['relay_on']);state='executed' if success or c['mode']=='mismatch' else 'failed';self.db.execute('UPDATE commands SET state=?,relay=?,error=? WHERE id=?',(state,relay,'' if success else '模拟拒绝/状态不一致',c['id']))
   self.db.commit();acks=self.rows("SELECT * FROM commands WHERE gateway_id=? AND state IN ('executed','failed') AND due<=?",(g['id'],now))
  for c in acks:
   # Persist execution before I/O; a network failure retries only the acknowledgement.
   self.request(g,'/api/gateway/ack',{'command_id':c['id'],'success':c['state']=='executed','relay_on':bool(c['relay'])})
   with self.lock:self.db.execute("UPDATE commands SET state='acknowledged' WHERE id=?",(c['id'],));self.event('ack','模拟执行回执已送达 '+c['id']);self.db.commit()
 def gateway_once(self,gid):
  with self.lock:
   g=self.gateway(gid,True)
   if not g['enabled']:return
   samples=[]
   for mapping in g['mappings']:
    m=self.meter(mapping['meter_id'])
    if not m['online']:continue
    m['sequence']+=1;self.write_meter(m);sample={'device_id':mapping['device_id'],'ts':time.time(),'sequence':m['sequence'],'power_w':m['power_w'],'voltage':m['voltage'],'pf':.95,'simulation':True}
    if m['controllable']:sample['relay_on']=m['relay_on']
    samples.append(sample)
   self.db.commit()
  try:
   accepted=self.request(g,'/api/gateway/telemetry',{'samples':samples}) if samples else {'accepted':0}
   commands=self.request(g,'/api/gateway/commands').get('commands',[]);self.claim(g,commands);self.execute_due(g)
   with self.lock:self.gateway_status[gid]={'state':'online','message':'模拟上报已接收 '+str(accepted.get('accepted',0))+' 条','updated_at':time.time()}
  except Exception:
   with self.lock:
    previous=self.gateway_status.get(gid,{});self.gateway_status[gid]={'state':'error','message':'连接或协议失败，请核对本机地址、令牌、设备映射和设备能力','updated_at':time.time()}
    if previous.get('state')!='error':self.event('error',g['name']+'：连接或协议失败（未记录令牌）');self.db.commit()
 def run(self):
  while not self.stop.is_set():
   try:self.tick();self.last_error=None
   except Exception:self.last_error='模拟计量更新失败'
   self.stop.wait(1)
 def gateways_run(self):
  while not self.stop.is_set():
   with self.lock:ids=[r['id'] for r in self.rows('SELECT id FROM gateways')]
   for gid in ids:
    if self.stop.is_set():break
    try:self.gateway_once(gid)
    except Exception:
     with self.lock:self.gateway_status[gid]={'state':'error','message':'配置无法读取，请重新填写令牌'}
   self.stop.wait(1)
 def close(self):
  self.stop.set()
  with self.lock:self.db.close()

class Server(ThreadingHTTPServer):
 daemon_threads=True;allow_reuse_address=True
 def __init__(self,address,app,web):self.app=app;self.web=Path(web).resolve();self.camera=CameraFeed(app,web);super().__init__(address,Handler)
 @property
 def origin(self):return f'http://127.0.0.1:{self.server_port}'
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,value,status=200,kind='application/json; charset=utf-8',cookie=None,extra=None):
  raw=value if isinstance(value,bytes) else jdump(value).encode();self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
  if cookie:self.send_header('Set-Cookie','tianshu_sim_session='+cookie+'; HttpOnly; SameSite=Strict; Path=/')
  for key,value in (extra or {}).items():self.send_header(key,str(value))
  self.end_headers();self.wfile.write(raw)
 def body(self):
  if self.headers.get('Transfer-Encoding'):raise Error('不支持此编码',400)
  size=int(self.headers.get('Content-Length',0))
  maximum=4*1024*1024+512 if urllib.parse.urlsplit(self.path).path=='/sim/camera-photo' else 131072
  if not 0<size<=maximum:raise Error('请求大小无效',413)
  if not self.headers.get('Content-Type','').startswith('application/json'):raise Error('需要 JSON',415)
  body=json.loads(self.rfile.read(size))
  if not isinstance(body,dict):raise Error('需要 JSON 对象')
  return body
 def do_OPTIONS(self):self.reply({'error':'不允许跨站调用'},403)
 def do_GET(self):self.handle_request('GET')
 def do_POST(self):self.handle_request('POST')
 def handle_request(self,method):
  try:
   self.connection.settimeout(15);a=self.server.app;path=urllib.parse.urlsplit(self.path).path
   if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':raise Error('请使用启动器的本机地址',403)
   if self.headers.get('Origin') and self.headers['Origin']!=self.server.origin:raise Error('不允许跨站调用',403)
   if path=='/api/health' and method=='GET':return self.reply({'ok':True,'version':VERSION,'heartbeat':a.heartbeat,'last_error':a.last_error,'simulation':True})
   if path.startswith('/mock/camera/'):
    if method!='GET':raise Error('监控接口只接受 GET',405)
    if path=='/mock/camera/stream.mjpeg':return self.server.camera.stream(self)
    if path=='/mock/camera/snapshot.jpg' or photo_index(path):
     index,raw=self.server.camera.frame(photo_index(path));return self.reply(raw,kind='image/jpeg',extra={'X-Simulation':'true','X-Frame-Index':index})
    raise Error('模拟画面不存在',404)
   if path.startswith('/mock/meters/'):
    if method=='POST':self.body()
    fault,value=a.api_read(path.rsplit('/',1)[-1],self.headers.get('Authorization',''))
    if fault=='timeout':time.sleep(12)
    if fault=='http500':return self.reply({'error':'模拟 HTTP 500','simulation':True},500)
    if fault=='business':return self.reply({'code':1001,'message':'模拟业务失败','simulation':True})
    if fault=='malformed':return self.reply(b'{invalid-simulation-json',kind='application/json')
    return self.reply(value)
   if path=='/sim/session' and method=='GET':
    cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));sid=cookie['tianshu_sim_session'].value if 'tianshu_sim_session' in cookie else '';now=time.time()
    with a.lock:
     a.sessions={k:v for k,v in a.sessions.items() if v['expires']>now}
     if sid not in a.sessions:
      if len(a.sessions)>=100:raise Error('本机会话过多',429)
      sid=secrets.token_urlsafe(32);a.sessions[sid]={'csrf':secrets.token_urlsafe(32),'expires':now+43200}
     return self.reply({'csrf':a.sessions[sid]['csrf'],'simulation':True},cookie=sid)
   if path.startswith('/sim/'):
    c=SimpleCookie();c.load(self.headers.get('Cookie',''));sid=c['tianshu_sim_session'].value if 'tianshu_sim_session' in c else ''
    with a.lock:session=a.sessions.get(sid)
    if not session or session['expires']<time.time():raise Error('本机会话过期，请刷新',401)
    if method=='POST' and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):raise Error('页面过期，请刷新',403)
    if path=='/sim/state' and method=='GET':return self.reply({**a.snapshot(),'camera':self.server.camera.status()})
    if path=='/sim/credentials' and method=='POST':self.body();return self.reply({'api_key':a.api_key()})
    if method=='POST':
     body=self.body()
     if path=='/sim/meters':return self.reply(a.save_meter(body))
     if path=='/sim/meter-action':return self.reply(a.change_meter(body))
     if path=='/sim/gateways':return self.reply(a.save_gateway(body))
     if path=='/sim/camera-settings':return self.reply(self.server.camera.configure(body))
     if path=='/sim/camera-photo':return self.reply(self.server.camera.upload(body))
    raise Error('模拟管理接口不存在',404)
   if method!='GET':raise Error('接口不存在',404)
   rel=urllib.parse.unquote(path).lstrip('/') or 'simulator.html';target=(self.server.web/rel).resolve()
   if not target.is_relative_to(self.server.web) or not target.is_file():raise Error('文件不存在',404)
   return self.reply(target.read_bytes(),kind=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
  except Error as error:self.reply({'error':str(error)},error.status)
  except (ConnectionError,TimeoutError):self.close_connection=True
  except (ValueError,TypeError,KeyError):self.reply({'error':'请求字段格式无效'},400)
  except Exception:self.reply({'error':'模拟服务处理失败'},500)

def main():
 p=argparse.ArgumentParser();p.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'simulator-data'));p.add_argument('--web-dir',default=str(Path(__file__).resolve().parents[1]/'web'));p.add_argument('--port',type=int,default=8792);p.add_argument('--parent-pipe',action='store_true');args=p.parse_args();a=Simulator(args.data_dir);server=Server(('127.0.0.1',args.port),a,args.web_dir);workers=[threading.Thread(target=a.run,daemon=True),threading.Thread(target=a.gateways_run,daemon=True)]
 for worker in workers:worker.start()
 print(jdump({'url':server.origin,'version':VERSION,'simulation':True}),flush=True)
 if args.parent_pipe:
  def parent_watch():sys.stdin.buffer.read();server.shutdown()
  threading.Thread(target=parent_watch,daemon=True).start()
 try:server.serve_forever(poll_interval=.5)
 except KeyboardInterrupt:pass
 finally:
  a.stop.set();server.server_close()
  for worker in workers:worker.join(20)
  if not any(w.is_alive() for w in workers):a.close()
if __name__=='__main__':main()
