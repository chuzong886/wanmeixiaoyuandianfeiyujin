"""Synthetic AI protocol tests. All provider traffic is mocked, never a paid AI call."""
import sys,tempfile,unittest,json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy,Error
from ai_analysis import endpoint_url
ADMIN={'id':'qa-admin','username':'synthetic-admin','role':'admin'}
BASE=ROOT/'build/ai-tests';BASE.mkdir(parents=True,exist_ok=True)

class AI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup);self.e=Energy(self.tmp.name,seed=False);self.addCleanup(self.e.close);self.sid=self.e.save_space(ADMIN,{'name':'Private-space-name'})['id'];self.e.save_device(ADMIN,{'space_id':self.sid,'name':'Private-device-name','area':'Private-area','device_type':'socket','nominal_w':1000,'threshold_w':2000});self.e.tick();self.ai=self.e.ai
 def config(self,**extra):return {'enabled':True,'endpoint':'https://api.example.invalid/v1','model':'synthetic-model','api_key':'test-key','prompt':'分析数据，不能编造数据。','days':14,'max_tokens':2048,'timeout':30,**extra}
 def prepare(self):self.ai.save(ADMIN,self.config());return self.ai.prepare(ADMIN,{'space_id':self.sid})
 def test_address_validation(self):
  self.assertEqual(endpoint_url('https://api.example.invalid/v1/'),'https://api.example.invalid/v1/chat/completions');self.assertEqual(endpoint_url('http://127.0.0.1:9000/v1/chat/completions'),'http://127.0.0.1:9000/v1/chat/completions')
  for url in ['http://example.invalid','https://user:secret@example.invalid/v1','https://example.invalid/v1?key=secret','file:///tmp/a','https://example.invalid:wrong/v1']:
   with self.assertRaises(Error):endpoint_url(url)
 def test_key_encrypted_not_returned(self):
  self.ai.save(ADMIN,self.config());raw=self.e.one("SELECT value FROM settings WHERE key='ai'")['value'];self.assertNotIn('test-key',raw);public=self.ai.settings(ADMIN);self.assertTrue(public['has_key']);self.assertNotIn('api_key',public)
 def test_blank_key_preserves_same_endpoint_not_other_provider(self):
  self.ai.save(ADMIN,self.config());self.ai.save(ADMIN,self.config(api_key='',prompt='新的提示词'))
  self.assertTrue(self.ai.settings(ADMIN)['has_key'])
  with self.assertRaises(Error):self.ai.save(ADMIN,self.config(api_key='',endpoint='https://another.example.invalid/v1'))
 def test_not_configured_no_fake_analysis(self):
  self.assertFalse(self.ai.status(ADMIN,self.sid)['configured'])
  with self.assertRaises(Error):self.ai.prepare(ADMIN,{'space_id':self.sid})
  self.assertEqual(self.e.rows('SELECT * FROM ai_runs'),[])
 def test_preview_anonymizes_and_sends_nothing(self):
  with patch.object(self.ai,'transport') as send:p=self.prepare();send.assert_not_called()
  payload=json.dumps(p,ensure_ascii=False);self.assertNotIn('Private-space-name',payload);self.assertNotIn('Private-device-name',payload);self.assertNotIn('test-key',payload);self.assertEqual(p['data']['data_source'],'demo');self.assertIsNone(p['data']['daily'][0]['kwh'])
 def test_names_only_when_explicitly_enabled(self):
  self.ai.save(ADMIN,self.config(include_names=True));p=self.ai.prepare(ADMIN,{'space_id':self.sid});self.assertEqual(p['data']['space'],'Private-space-name');self.assertTrue(p['data']['scope']['names_included'])
 def test_confirmation_and_token_required(self):
  p=self.prepare()
  with self.assertRaises(Error):self.ai.analyze(ADMIN,{'token':p['token']})
  with self.assertRaises(Error):self.ai.analyze(ADMIN,{'token':'invalid','confirm_send':True})
 def test_token_bound_to_user(self):
  p=self.prepare()
  with self.assertRaises(Error):self.ai.analyze({**ADMIN,'id':'another-user'},{'token':p['token'],'confirm_send':True})
 def test_once_only_and_real_transport_contract(self):
  p=self.prepare();conn=MagicMock();response=conn.getresponse.return_value;response.status=200;response.read.return_value=json.dumps({'choices':[{'message':{'content':'合成接口响应，不是真实模型结论。'}}]}).encode()
  with patch('ai_analysis.http.client.HTTPSConnection',return_value=conn):
   result=self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
  self.assertEqual(result['state'],'succeeded');self.assertIn('合成接口响应',result['answer']);args=conn.request.call_args.args;self.assertEqual(args[0:2],('POST','/v1/chat/completions'));body=json.loads(args[2]);self.assertEqual(body['model'],'synthetic-model');self.assertFalse(body['stream']);self.assertEqual(body['max_tokens'],2048);self.assertEqual(args[3]['Authorization'],'Bearer test-key');self.assertNotIn('test-key',args[2].decode())
  with self.assertRaises(Error):self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
  self.assertEqual(conn.request.call_count,1)
 def test_configuration_change_invalidates_preview(self):
  p=self.prepare();self.ai.save(ADMIN,self.config(model='other-model'))
  with self.assertRaises(Error):self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
 def test_failed_call_is_recorded_no_retry_or_fake_fallback(self):
  p=self.prepare()
  with patch.object(self.ai,'transport',side_effect=Error('AI 接口限流',502)) as send:
   with self.assertRaises(Error):self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
   self.assertEqual(send.call_count,1)
  row=self.e.one('SELECT * FROM ai_runs');self.assertEqual(row['state'],'failed');self.assertEqual(row['answer'],'');self.assertFalse(self.ai.inflight)
 def test_network_does_not_hold_collector_lock(self):
  p=self.prepare();started=threading.Event();finish=threading.Event();errors=[]
  def slow(c,m):started.set();finish.wait(3);return '合成延迟响应'
  def work():
   try:self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
   except Exception as error:errors.append(error)
  with patch.object(self.ai,'transport',side_effect=slow):
   thread=threading.Thread(target=work);thread.start();self.assertTrue(started.wait(2));acquired=self.e.lock.acquire(timeout=.2)
   try:self.assertTrue(acquired)
   finally:
    if acquired:self.e.lock.release()
    finish.set();thread.join(3)
  self.assertFalse(errors)
 def test_viewer_and_foreign_space_denied(self):
  viewer={'id':'viewer','username':'viewer','role':'viewer'}
  with self.assertRaises(Error):self.ai.settings(viewer)
  with self.assertRaises(Error):self.ai.prepare(viewer,{'space_id':self.sid})
  with self.assertRaises(Error):self.ai.history(viewer,self.sid)
 def test_provider_auth_error_is_safe(self):
  p=self.prepare();conn=MagicMock();conn.getresponse.return_value.status=401;conn.getresponse.return_value.read.return_value=b'server secret'
  with patch('ai_analysis.http.client.HTTPSConnection',return_value=conn):
   with self.assertRaises(Error) as error:self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
  self.assertNotIn('server secret',str(error.exception));self.assertNotIn('test-key',str(error.exception))
 def test_actual_loopback_http_transport(self):
  received=[]
  class Provider(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_POST(self):
    received.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
    raw=json.dumps({'choices':[{'message':{'content':'本机合成协议响应，仅用于测试。'}}]}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  provider=ThreadingHTTPServer(('127.0.0.1',0),Provider);thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
  try:
   self.ai.save(ADMIN,self.config(endpoint='http://127.0.0.1:'+str(provider.server_port)+'/v1'));p=self.ai.prepare(ADMIN,{'space_id':self.sid});result=self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True});self.assertEqual(result['state'],'succeeded');self.assertEqual(len(received),1);self.assertEqual(received[0][0],'/v1/chat/completions');self.assertEqual(received[0][1],'Bearer test-key');self.assertEqual(received[0][2]['messages'][0]['content'],p['prompt']);self.assertIn('"schema":"tianshu.ai-summary.v1"',received[0][2]['messages'][1]['content'])
  finally:provider.shutdown();provider.server_close();thread.join(2)
 def test_error_does_not_persist_key(self):
  p=self.prepare()
  with patch.object(self.ai,'transport',side_effect=Error('合成错误 test-key',502)):
   with self.assertRaises(Error) as error:self.ai.analyze(ADMIN,{'token':p['token'],'confirm_send':True})
  self.assertNotIn('test-key',str(error.exception));self.assertNotIn('test-key',json.dumps(self.ai.history(ADMIN,self.sid)))

if __name__=='__main__':unittest.main()
