"""Synthetic loopback integration only: no real devices, accounts or external traffic."""
import sys,unittest,tempfile,threading,time,json,http.client,uuid
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from simulator import Simulator,Server as SimServer,local_target
from server import Server as EnergyServer
from energy import Energy,Error,clock
BASE=ROOT/'build/simulator-tests';BASE.mkdir(parents=True,exist_ok=True)
ADMIN={'id':'synthetic-admin','username':'synthetic-admin','role':'admin'}
class Lab(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup);self.s=Simulator(Path(self.tmp.name)/'sim');self.addCleanup(self.s.close);self.e=Energy(Path(self.tmp.name)/'energy',seed=False);self.addCleanup(self.e.close)
  self.mid=self.s.snapshot()['meters'][0]['id'];self.sid=self.e.save_space(ADMIN,{'name':'模拟测试专用空间'})['id'];self.g=self.e.create_gateway(ADMIN,{'name':'【模拟】专用网关','space_id':self.sid});self.d=self.e.save_device(ADMIN,{'space_id':self.sid,'name':'【模拟】测试插座','area':'隔离测试','device_type':'socket','source':'gateway','gateway_id':self.g['id'],'nominal_w':1000,'threshold_w':2000})
  self.ss=SimServer(('127.0.0.1',0),self.s,ROOT/'web');self.es=EnergyServer(('127.0.0.1',0),self.e,ROOT/'web')
  for server in (self.ss,self.es):
   thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(lambda server=server,thread=thread:(server.shutdown(),server.server_close(),thread.join(3)))
 def config(self,**extra):return {'name':'模拟网关','target':self.es.origin,'token':self.g['token'],'enabled':True,'simulation_only':True,'ack_mode':'success','ack_delay':0,'mappings':[{'meter_id':self.mid,'device_id':self.d['id']}],**extra}
 def gateway(self,**extra):return self.s.save_gateway(self.config(**extra))
 def command(self):
  self.e.tick();return self.e.command(ADMIN,{'device_id':self.d['id'],'desired':False,'reason':'synthetic test','confirm_name':self.d['name'],'idempotency':uuid.uuid4().hex})
 def call(self,path,body=None,headers=None):
  c=http.client.HTTPConnection('127.0.0.1',self.ss.server_port,timeout=3)
  try:
   c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),{'Content-Type':'application/json',**(headers or {})});r=c.getresponse();raw=r.read();return r.status,raw,r.getheader('Set-Cookie')
  finally:c.close()
 def test_balance_and_cumulative_follow_consumption(self):
  m=self.s.meter(self.mid);self.s.save_meter({**m,'profile':'constant','nominal_w':3600000});self.s.tick(self.s.last_tick+2);n=self.s.meter(self.mid);self.assertAlmostEqual(n['balance_kwh'],m['balance_kwh']-2,places=5);self.assertAlmostEqual(n['cumulative_kwh'],m['cumulative_kwh']+2,places=5)
 def test_recharge_debt_reset(self):
  self.s.change_meter({'id':self.mid,'action':'balance','amount':-5});self.s.change_meter({'id':self.mid,'action':'recharge','amount':10});self.assertEqual(self.s.meter(self.mid)['balance_kwh'],5)
  with self.assertRaises(Error):self.s.change_meter({'id':self.mid,'action':'reset'})
  self.s.change_meter({'id':self.mid,'action':'reset','confirmation':'清零模拟电表'});self.assertEqual(self.s.meter(self.mid)['cumulative_kwh'],0)
 def test_mode_hides_inexistent_fields(self):
  m=self.s.save_meter({**self.s.meter(self.mid),'mode':'balance'});r=self.s.reading(m);self.assertIn('balance_kwh',r);self.assertNotIn('power_w',r);self.assertNotIn('cumulative_kwh',r)
 def test_edit_does_not_recharge(self):
  m=self.s.meter(self.mid);self.s.save_meter({**m,'balance_kwh':9999,'cumulative_kwh':9999});self.assertEqual(self.s.meter(self.mid)['balance_kwh'],m['balance_kwh']);self.assertEqual(self.s.meter(self.mid)['cumulative_kwh'],m['cumulative_kwh'])
 def test_frozen_api(self):
  m=self.s.save_meter({**self.s.meter(self.mid),'fault':'frozen'});_,a=self.s.api_read(self.mid,'Bearer '+self.s.api_key());self.s.tick(self.s.last_tick+3);_,b=self.s.api_read(self.mid,'Bearer '+self.s.api_key());self.assertEqual(a,b)
 def test_api_http_auth_and_fields(self):
  path='/mock/meters/'+self.mid;self.assertEqual(self.call(path)[0],401);code,raw,_=self.call(path,{},headers={'Authorization':'Bearer '+self.s.api_key()});self.assertEqual(code,200);v=json.loads(raw);self.assertTrue(v['simulation']);self.assertIn('power_w',v['data'])
 def test_http_fault_modes(self):
  for fault in ('http500','business','malformed'):
   self.s.save_meter({**self.s.meter(self.mid),'fault':fault});code,raw,_=self.call('/mock/meters/'+self.mid,headers={'Authorization':'Bearer '+self.s.api_key()})
   if fault=='http500':self.assertEqual(code,500)
   elif fault=='business':self.assertNotEqual(json.loads(raw)['code'],0)
   else:
    with self.assertRaises(ValueError):json.loads(raw)
 def test_management_requires_session_csrf_and_origin(self):
  self.assertEqual(self.call('/sim/state')[0],401);_,raw,cookie=self.call('/sim/session');csrf=json.loads(raw)['csrf'];headers={'Cookie':cookie.split(';')[0]};self.assertEqual(self.call('/sim/meters',{},headers)[0],403);self.assertEqual(self.call('/sim/meters',{'name':'恶意'}, {**headers,'X-CSRF-Token':csrf,'Origin':'https://evil.invalid'})[0],403);self.assertEqual(self.call('/sim/state',headers={**headers,'Host':'evil.invalid'})[0],403)
 def test_no_secrets_in_public_state_and_encrypted_disk(self):
  g=self.gateway();self.assertNotIn(self.g['token'],json.dumps(self.s.snapshot()));raw=self.s.db.execute('SELECT config FROM gateways WHERE id=?',(g['id'],)).fetchone()[0];self.assertNotIn(self.g['token'],raw);self.assertNotIn(self.s.api_key(),self.s.db.execute("SELECT value FROM settings WHERE key='api-key'").fetchone()[0])
 def test_only_local_targets_and_enable_confirmation(self):
  for value in ('https://example.invalid','http://localhost:8791','http://127.0.0.1:8791/api','http://user:pass@127.0.0.1:8791'):
   with self.assertRaises(Error):local_target(value)
  with self.assertRaises(Error):self.gateway(simulation_only=False)
 def test_gateway_success_is_real_http_ack_not_request_success(self):
  g=self.gateway();self.s.tick();self.s.gateway_once(g['id']);self.assertEqual(self.s.gateway_status[g['id']]['state'],'online');c=self.command();self.assertEqual(c['state'],'queued');self.s.gateway_once(g['id']);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'confirmed');self.assertFalse(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.s.snapshot()['commands'][0]['state'],'acknowledged')
 def test_failure_preserves_relay(self):
  g=self.gateway(ack_mode='failure');self.s.tick();self.s.gateway_once(g['id']);c=self.command();self.s.gateway_once(g['id']);self.assertTrue(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'rejected')
 def test_timeout_does_not_execute_or_fake_ack(self):
  g=self.gateway(ack_mode='timeout');self.s.tick();self.s.gateway_once(g['id']);c=self.command();self.s.gateway_once(g['id']);self.assertTrue(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'dispatched');self.assertEqual(self.s.snapshot()['commands'][0]['state'],'noack')
 def test_mismatch_rejected(self):
  g=self.gateway(ack_mode='mismatch');self.s.tick();self.s.gateway_once(g['id']);c=self.command();self.s.gateway_once(g['id']);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'rejected')
 def test_offline_sends_no_fake_online_sample(self):
  g=self.gateway();self.s.save_meter({**self.s.meter(self.mid),'online':False});self.s.gateway_once(g['id']);self.assertEqual(self.e.device(self.d['id'])['last_sample'],0)
 def test_readonly_omits_relay_and_is_accepted(self):
  readonly=self.e.save_device(ADMIN,{'space_id':self.sid,'name':'【模拟】只读表','area':'测试','device_type':'readonly_meter','source':'gateway','gateway_id':self.g['id'],'nominal_w':1000,'threshold_w':2000});self.s.save_meter({**self.s.meter(self.mid),'controllable':False});g=self.gateway(mappings=[{'meter_id':self.mid,'device_id':readonly['id']}]);self.s.tick();self.s.gateway_once(g['id']);self.assertEqual(self.s.gateway_status[g['id']]['state'],'online');self.assertIsNone(self.e.devices(self.sid)[1 if self.e.devices(self.sid)[0]['id']!=readonly['id'] else 0]['relay_on'])
 def test_duplicate_command_not_executed_twice(self):
  g=self.gateway();secret=self.s.gateway(g['id'],True);c={'id':'deduplicated','device_id':self.d['id'],'desired':False,'expires':clock()+30};self.s.claim(secret,[c,c]);self.assertEqual(len(self.s.snapshot()['commands']),1)
 def test_sequence_persists_after_restart(self):
  g=self.gateway();self.s.tick();self.s.gateway_once(g['id']);before=self.s.meter(self.mid)['sequence'];other=Simulator(self.s.directory)
  try:other.gateway_once(g['id']);self.assertGreater(other.meter(self.mid)['sequence'],before);self.assertEqual(other.gateway_status[g['id']]['state'],'online')
  finally:other.close()
 def test_api_connected_to_real_tianshu_adapter(self):
  c={'url':self.ss.origin+'/mock/meters/'+self.mid,'method':'GET','headers':{'Authorization':'Bearer '+self.s.api_key()},'mapping':{'balance_kwh':'data.balance_kwh','cumulative_kwh':'data.cumulative_kwh','power_w':'data.power_w'},'success_path':'code','success_value':'0','enabled':False}
  d=self.e.save_device(ADMIN,{'space_id':self.sid,'name':'【模拟】API表','area':'测试','device_type':'readonly_meter','source':'third_party','nominal_w':1000,'threshold_w':2000,'api_config':c});self.s.tick();self.e.api.query(ADMIN,d['id']);row=self.e.one('SELECT * FROM api_readings WHERE device_id=?',(d['id'],));self.assertIsNotNone(row);self.assertAlmostEqual(row['balance'],self.s.meter(self.mid)['balance_kwh'],places=5)
 def test_failed_ack_retries_only_ack_not_execution(self):
  g=self.gateway();secret=self.s.gateway(g['id'],True);c={'id':'ack-retry','device_id':self.d['id'],'desired':False,'expires':clock()+30};self.s.claim(secret,[c])
  with patch.object(self.s,'request',side_effect=OSError('synthetic network failure')):
   with self.assertRaises(OSError):self.s.execute_due(secret)
  self.assertFalse(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.s.meter(self.mid)['power_w'],0);self.s.change_meter({'id':self.mid,'action':'relay','relay_on':True})
  with patch.object(self.s,'request',return_value={'state':'confirmed'}) as request:self.s.execute_due(secret);self.assertEqual(request.call_count,1)
  self.assertTrue(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.s.snapshot()['commands'][0]['state'],'acknowledged')
 def test_expired_command_never_executes(self):
  g=self.gateway();secret=self.s.gateway(g['id'],True);self.s.claim(secret,[{'id':'expired','device_id':self.d['id'],'desired':False,'expires':clock()-1}]);self.s.execute_due(secret);self.assertTrue(self.s.meter(self.mid)['relay_on']);self.assertEqual(self.s.snapshot()['commands'][0]['state'],'expired')
 def test_restart_marks_pending_unknown_without_replay(self):
  g=self.gateway();self.s.claim(self.s.gateway(g['id'],True),[{'id':'restart-unknown','device_id':self.d['id'],'desired':False,'expires':clock()+30}]);other=Simulator(self.s.directory)
  try:other.execute_due(other.gateway(g['id'],True));self.assertTrue(other.meter(self.mid)['relay_on']);self.assertEqual(other.snapshot()['commands'][0]['state'],'unknown')
  finally:other.close()

if __name__=='__main__':unittest.main(verbosity=2)
