"""Same-origin local HTTP application. Standard library only."""
from __future__ import annotations
import argparse, csv, io, json, logging, mimetypes, os, secrets, socket, sys, threading, time, urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from energy import Energy,Error,jdump,clock,VERSION

class Server(ThreadingHTTPServer):
 daemon_threads=True;allow_reuse_address=True
 def __init__(self,address,energy,web):
  self.energy=energy;self.web=Path(web).resolve();self.login_attempts={};self.live_limit=threading.BoundedSemaphore(12);super().__init__(address,Handler)
 @property
 def origin(self):return f'http://127.0.0.1:{self.server_port}'
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1';server_version='TianshuEnergy/'+VERSION
 def log_message(self,*args):pass
 def do_GET(self):self.handle_request('GET')
 def do_POST(self):self.handle_request('POST')
 def do_OPTIONS(self):self.reply({'error':'不允许跨站调用'},403)
 def reply(self,value,status=200,cookie=None):
  raw=jdump(value).encode();self.bytes(raw,'application/json; charset=utf-8',status,cookie)
 def headers_out(self,status,kind,length=None,cookie=None):
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: http: https:; media-src 'self' blob: http: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
  if length is not None:self.send_header('Content-Length',str(length))
  if cookie:self.send_header('Set-Cookie',f'tianshu_session={cookie}; HttpOnly; SameSite=Strict; Path=/')
  self.end_headers()
 def bytes(self,raw,kind,status=200,cookie=None):
  self.headers_out(status,kind,len(raw),cookie);self.wfile.write(raw)
 def cookie(self):
  c=SimpleCookie()
  try:c.load(self.headers.get('Cookie',''));return c['tianshu_session'].value if 'tianshu_session' in c else None
  except Exception:return None
 def body(self):
  if self.headers.get('Transfer-Encoding'):raise Error('不支持此传输编码',400)
  length=int(self.headers.get('Content-Length',0))
  maximum=48*1024*1024 if self.path=='/api/restore' else 262144
  if not 0<length<=maximum:raise Error('请求大小无效',413)
  if not self.headers.get('Content-Type','').startswith('application/json'):raise Error('请求必须是JSON',415)
  value=json.loads(self.rfile.read(length))
  if not isinstance(value,dict):raise Error('请求必须是JSON对象')
  return value
 def handle_request(self,method):
  try:
   self.connection.settimeout(15)
   # Exact host validation prevents DNS rebinding; the launcher always prints the IPv4 URL.
   if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':raise Error('请使用程序提供的本机地址',403)
   origin=self.headers.get('Origin')
   if origin and origin!=self.server.origin:raise Error('不允许跨站调用',403)
   parsed=urllib.parse.urlsplit(self.path);path=parsed.path;params={k:v[-1] for k,v in urllib.parse.parse_qs(parsed.query).items()};b=self.body() if method=='POST' else {};app=self.server.energy
   if path=='/api/health' and method=='GET':return self.reply({'ok':True,'version':VERSION,'heartbeat':app.last_tick,'last_error':app.last_error})
   if path=='/api/session' and method=='GET':
    result,cookie=app.boot(self.cookie());return self.reply(result,cookie=cookie)
   if path=='/api/login' and method=='POST':
    key=self.client_address[0];recent=[t for t in self.server.login_attempts.get(key,[]) if clock()-t<60]
    if len(recent)>=10:raise Error('登录尝试过多，请稍后再试',429)
    self.server.login_attempts[key]=recent+[clock()];result,cookie=app.login(b);return self.reply(result,cookie=cookie)
   if path.startswith('/api/gateway/'):
    token=self.headers.get('Authorization','')
    if not token.startswith('Bearer '):raise Error('缺少网关认证',401)
    with app.lock:
     g=app.gateway(token[7:])
     if path=='/api/gateway/telemetry' and method=='POST':return self.reply(app.ingest(g,b))
     if path=='/api/gateway/commands' and method=='GET':return self.reply(app.gateway_commands(g))
     if path=='/api/gateway/ack' and method=='POST':return self.reply(app.gateway_ack(g,b))
    raise Error('网关接口不存在',404)
   if path.startswith('/api/'):
    session=app.get_session(self.cookie());user=session['user']
    if method=='POST' and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):raise Error('页面已过期，请刷新',403)
    if path=='/api/logout' and method=='POST':app.sessions.pop(self.cookie(),None);return self.reply({'ok':True},cookie='expired')
    if path=='/api/ai-analyze' and method=='POST':return self.reply(app.ai.analyze(user,b))
    if path=='/api/device-api-config' and method=='GET':
     app.require(user,('admin',))
     with app.lock:return self.reply({'config':app.api.config(params.get('device_id',''))})
    if path=='/api/device-history' and method=='GET':
     with app.lock:
      app.access(user,app.device(params.get('device_id',''))['space_id']);return self.reply(app.api.history(user,params.get('device_id','')))
    if path=='/api/device-query' and method=='POST':return self.reply(app.api.query(user,b.get('device_id','')))
    if path=='/api/device-capture' and method=='POST':
     app.require(user,('admin',));return self.reply(app.api.adapter('capture',source=str(b.get('source',''))))
    if path=='/api/stream' and method=='GET':return self.stream(params,session)
    if path=='/api/export' and method=='GET':return self.export(params,user)
    if path=='/api/backup' and method=='GET':
     app.require(user,('admin',))
     import sqlite3
     with app.lock:
      copy=sqlite3.connect(':memory:')
      try:app.db.backup(copy);raw=copy.serialize();raw=raw[:18]+bytes((1,1))+raw[20:]
      finally:copy.close()
     self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Disposition','attachment; filename="tianshu-v1-backup.sqlite"');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
    return self.reply(app.dispatch(method,path,user,b,params))
   if method!='GET':raise Error('接口不存在',404)
   rel=urllib.parse.unquote(path).lstrip('/') or 'index.html'
   if rel.endswith('/'):rel+='index.html'
   target=(self.server.web/rel).resolve()
   if not target.is_relative_to(self.server.web) or not target.is_file():raise Error('文件不存在',404)
   self.bytes(target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
  except Error as e:
   with self.server.energy.lock:self.server.energy.db.rollback()
   self.reply({'error':str(e)},e.status)
  except (ConnectionError,TimeoutError,socket.timeout):self.close_connection=True
  except (ValueError,TypeError,KeyError):
   with self.server.energy.lock:self.server.energy.db.rollback()
   self.reply({'error':'请求字段格式无效，请核对填写内容'},400)
  except Exception:
   with self.server.energy.lock:self.server.energy.db.rollback()
   self.reply({'error':'本地服务处理失败，请稍后重试'},500)
 def stream(self,params,session):
  if not self.server.live_limit.acquire(False):raise Error('实时连接过多，请关闭多余页面',429)
  try:
   sid=params.get('space_id','space-demo');self.server.energy.space(sid);self.server.energy.access(session['user'],sid);self.headers_out(200,'text/event-stream; charset=utf-8');self.wfile.write(b'retry: 2000\n\n');self.wfile.flush()
   session_id=self.cookie()
   for _ in range(3600):
    if self.server.energy.sessions.get(session_id) is not session:break
    if session['expires']<clock():break
    if self.server.energy.stop.is_set():break
    data=self.server.energy.snapshot(sid);raw=('event: snapshot\nid: '+str(int(clock()*1000))+'\ndata: '+jdump(data)+'\n\n').encode();self.wfile.write(raw);self.wfile.flush();self.server.energy.stop.wait(1)
  except (ConnectionError,socket.timeout):pass
  finally:self.close_connection=True;self.server.live_limit.release()
 def export(self,params,user):
  sid=params.get('space_id','space-demo');self.server.energy.access(user,sid);reports=self.server.energy.reports(sid,params.get('days',7));out=io.StringIO();writer=csv.writer(out);writer.writerow(['日期','用电量(kWh)','估算费用(CNY)','有效覆盖小时'])
  for row in reports['daily']:writer.writerow([row['date'],round(row['kwh'],4) if row['kwh'] is not None else '',round(row['cost'],2) if row['cost'] is not None else '',round(row['covered_hours'],3)])
  raw=('\ufeff'+out.getvalue()).encode();self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="energy-report.csv"');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)

def main():
 p=argparse.ArgumentParser();p.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'data'));p.add_argument('--web-dir',default=str(Path(__file__).resolve().parents[1]/'web'));p.add_argument('--port',type=int,default=8790);p.add_argument('--parent-pipe',action='store_true');args=p.parse_args();app=Energy(args.data_dir)
 settings=app.one("SELECT value FROM settings WHERE key='backend'");configured=json.loads(settings['value']).get('port',0) if settings else 0
 server=Server(('127.0.0.1',configured or args.port),app,args.web_dir);worker=threading.Thread(target=app.run,daemon=True);worker.start();print(jdump({'url':server.origin,'version':VERSION}),flush=True)
 if args.parent_pipe:
  def parent_watch():
   sys.stdin.buffer.read();server.shutdown()
  threading.Thread(target=parent_watch,daemon=True).start()
 try:server.serve_forever(poll_interval=.5)
 except KeyboardInterrupt:pass
 finally:app.stop.set();server.server_close();worker.join(5);app.close()
if __name__=='__main__':main()
