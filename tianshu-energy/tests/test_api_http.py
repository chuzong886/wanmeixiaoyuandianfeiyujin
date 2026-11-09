"""End-to-end HTTP tests. Both servers bind loopback; no campus endpoint is contacted."""
import sys,unittest,tempfile,json,threading,http.client,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy
from server import Server
BASE=ROOT/'build/http-api-tests';BASE.mkdir(parents=True,exist_ok=True)
class Upstream(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  raw=self.rfile.read(int(self.headers.get('Content-Length',0)));self.server.requests.append((self.path,self.headers.get('Content-Type'),self.headers.get('Authorization'),raw))
  if self.path=='/redirect':self.send_response(302);self.send_header('Location','https://example.com/never-follow');self.send_header('Content-Length','0');self.end_headers();return
  raw=json.dumps({'code':0,'data':{'balance':17.25}}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
class HTTP(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.e=Energy(self.tmp.name);self.server=Server(('127.0.0.1',0),self.e,ROOT/'web');self.up=ThreadingHTTPServer(('127.0.0.1',0),Upstream);self.up.requests=[]
  for s in [self.server,self.up]:threading.Thread(target=s.serve_forever,daemon=True).start()
  self.cookie='';self.csrf='';self.request('GET','/api/session');self.request('POST','/api/setup',{'username':'owner','password':'Synthetic-password-012345'});self.request('POST','/api/login',{'username':'owner','password':'Synthetic-password-012345'});self.request('GET','/api/session')
 def tearDown(self):
  for s in [self.server,self.up]:s.shutdown();s.server_close()
  self.e.close();self.tmp.cleanup()
 def request(self,method,path,body=None,cookie=None,csrf=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=20);headers={'Cookie':self.cookie if cookie is None else cookie,'Content-Type':'application/json','X-CSRF-Token':self.csrf if csrf is None else csrf};c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers);r=c.getresponse();raw=r.read();status=r.status
  if r.getheader('Set-Cookie'):self.cookie=r.getheader('Set-Cookie').split(';')[0]
  value=json.loads(raw)
  if path=='/api/session':self.csrf=value.get('csrf','')
  c.close();return status,value
 def add(self,**extra):
  conf={'url':f'http://127.0.0.1:{self.up.server_port}/query','method':'POST','encoding':'json','headers':{'Authorization':'Bearer synthetic'},'body':{'account':'TST'},'mapping':{'balance_kwh':'data.balance'},'success_path':'code','success_value':'0','enabled':False,**extra}
  status,d=self.request('POST','/api/devices',{'space_id':'space-demo','name':'HTTP第三方电表','area':'合成区域','kind':'meter','source':'third_party','nominal_w':1000,'threshold_w':2000,'api_config':conf});self.assertEqual(status,200,d);return d
 def test_post_device_enters_same_dashboard_and_history(self):
  d=self.add();self.assertEqual(self.up.requests,[]);status,r=self.request('POST','/api/device-query',{'device_id':d['id']});self.assertEqual(status,200,r);self.assertTrue(r['ok']);request=self.up.requests[0];self.assertIn('application/json',request[1]);self.assertEqual(request[2],'Bearer synthetic');self.assertEqual(json.loads(request[3])['account'],'TST');status,s=self.request('GET','/api/snapshot');meter=next(m for m in s['devices'] if m['id']==d['id']);self.assertEqual(meter['api']['reading']['balance'],17.25);self.assertFalse(meter['has_power']);self.assertNotIn('TST',json.dumps(s));status,h=self.request('GET','/api/device-history?device_id='+d['id']);self.assertEqual(h['readings'][0]['balance'],17.25)
 def test_form_post_is_really_urlencoded(self):
  d=self.add(encoding='form');status,r=self.request('POST','/api/device-query',{'device_id':d['id']});self.assertEqual(status,200,r);self.assertIn('application/x-www-form-urlencoded',self.up.requests[0][1]);self.assertEqual(urllib.parse.parse_qs(self.up.requests[0][3].decode())['account'],['TST'])
 def test_redirect_not_followed_and_no_false_reading(self):
  d=self.add(url=f'http://127.0.0.1:{self.up.server_port}/redirect');status,r=self.request('POST','/api/device-query',{'device_id':d['id']});self.assertEqual(status,502);self.assertEqual(len(self.up.requests),1);self.assertEqual(self.e.rows('SELECT * FROM api_readings'),[])
 def test_query_requires_login_csrf_and_admin(self):
  d=self.add();self.assertEqual(self.request('POST','/api/device-query',{'device_id':d['id']},cookie='')[0],401);self.assertEqual(self.request('POST','/api/device-query',{'device_id':d['id']},csrf='bad')[0],403);_,session=self.e.make_session({'username':'reader','role':'viewer'});token=next(k for k,v in self.e.sessions.items() if v is session);self.assertEqual(self.request('GET','/api/device-api-config?device_id='+d['id'],cookie='tianshu_session='+token)[0],403);self.assertEqual(self.request('POST','/api/device-query',{'device_id':d['id']},cookie='tianshu_session='+token,csrf=session['csrf'])[0],403);self.assertEqual(self.up.requests,[])
 def test_balance_only_api_never_gets_remote_switch(self):
  d=self.add();status,r=self.request('POST','/api/command',{'device_id':d['id'],'desired':False,'reason':'test','confirm_name':d['name'],'idempotency':'no-control'});self.assertEqual(status,409);self.assertEqual(self.up.requests,[])
if __name__=='__main__':unittest.main(verbosity=2)
