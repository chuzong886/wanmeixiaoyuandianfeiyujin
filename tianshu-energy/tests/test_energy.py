"""Offline domain and optional loopback integration tests. No physical hardware or outbound traffic."""
import sys,unittest,tempfile,time,uuid,math,json,threading,http.client,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy,Error,tariff_default,local,clock
from server import Server
BASE=ROOT/'build/tests';BASE.mkdir(parents=True,exist_ok=True)
HTTP='--http' in sys.argv
if HTTP:sys.argv.remove('--http')
ADMIN={'id':'admin-test','username':'test-owner','role':'admin'};DEMO={'id':'demo','username':'test-demo','role':'demo'};VIEWER={'id':'view','username':'test-viewer','role':'viewer'}
class Domain(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);assert Path(self.tmp.name).resolve().is_relative_to(BASE.resolve());self.addCleanup(self.tmp.cleanup);self.e=Energy(self.tmp.name,seed=False);self.addCleanup(self.e.close);self.space=self.e.save_space(DEMO,{'name':'任意用途空间','description':'不限定行业'});self.sid=self.space['id'];self.d=self.add_device()
 def add_device(self,**extra):return self.e.save_device(DEMO,{'space_id':self.sid,'name':'试验回路','area':'自由区域','kind':'circuit','nominal_w':1000,'threshold_w':2000,**extra})
 def command(self,**extra):
  self.e.tick();return self.e.command(DEMO,{'device_id':self.d['id'],'desired':False,'reason':'synthetic verification','confirm_name':self.d['name'],'idempotency':uuid.uuid4().hex,**extra})
 def test_arbitrary_spaces_have_same_features(self):
  s=self.e.save_space(DEMO,{'name':'海上临时工作点','description':'可自由定义'});self.assertNotEqual(s['id'],self.sid);self.assertEqual(len(s['tariff']['prices']),24);self.assertEqual(self.e.snapshot(s['id'])['device_count'],0)
 def test_current_is_single_or_three_phase(self):
  self.e.tick();d=self.e.devices(self.sid)[0];self.assertAlmostEqual(d['current_a'],d['power_w']/(d['voltage']*d['pf']))
  self.e.save_device(DEMO,{**d,'space_id':self.sid,'phases':3,'nominal_voltage':380});self.e.tick();d=self.e.devices(self.sid)[0];self.assertAlmostEqual(d['current_a'],d['power_w']/(math.sqrt(3)*d['voltage']*d['pf']));self.assertTrue(375<d['voltage']<385)
 def test_observation_only_device_not_double_counted(self):
  other=self.add_device(name='子级观察电表',counted=False);self.e.tick();s=self.e.snapshot(self.sid);a=next(d for d in s['devices'] if d['id']==self.d['id']);self.assertAlmostEqual(s['power_kw'],a['power_w']/1000);self.assertEqual(len(s['devices']),2)
 def test_switch_waits_for_feedback_then_zero_power(self):
  c=self.command();self.assertEqual(c['state'],'queued');self.assertTrue(self.e.device(self.d['id'])['relay_on']);self.e.tick(c['created']+1.2);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'confirmed');self.assertEqual(self.e.device(self.d['id'])['power_w'],0)
 def test_protected_circuit_cannot_be_controlled(self):
  self.e.save_device(DEMO,{**self.d,'protected':True,'space_id':self.sid});self.e.tick()
  with self.assertRaises(Error):self.command()
  self.assertEqual(self.e.rows('SELECT * FROM commands'),[])
 def test_viewer_cannot_send_commands(self):
  self.e.tick()
  with self.assertRaises(Error):self.e.command(VIEWER,{'device_id':self.d['id'],'desired':False})
 def test_duplicate_command_does_not_repeat(self):
  key=uuid.uuid4().hex;c=self.command(idempotency=key);again=self.command(idempotency=key);self.assertEqual(c['id'],again['id']);self.assertEqual(len(self.e.rows('SELECT * FROM commands')),1)
 def test_device_name_confirmation_required(self):
  with self.assertRaises(Error):self.command(confirm_name='another-device')
 def test_pending_command_blocks_another_request(self):
  self.command()
  with self.assertRaises(Error):self.command()
 def test_command_expiration_does_not_change_relay(self):
  c=self.command();self.e.tick(c['expires']+1);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'expired');self.assertTrue(self.e.device(self.d['id'])['relay_on'])
 def test_hour_boundary_energy_is_split(self):
  self.e.db.execute('DELETE FROM hourly');end=int(clock()//3600)*3600+2;self.e.add_sample(self.d,3600,220,1,True,end,4);rows=self.e.rows('SELECT * FROM hourly ORDER BY hour');self.assertEqual(len(rows),2);self.assertAlmostEqual(sum(x['energy_wh'] for x in rows),4);self.assertAlmostEqual(rows[0]['energy_wh'],2)
 def test_tariff_versions_preserve_historical_cost_without_changing_energy(self):
  h=int(clock()//3600)*3600;self.e.db.execute('UPDATE devices SET created_at=0 WHERE id=?',(self.d['id'],));self.e.db.execute('UPDATE scopes SET effective=0 WHERE device_id=?',(self.d['id'],));self.e.db.execute('INSERT INTO hourly VALUES(?,?,?,?,?)',(self.d['id'],h,2000,3600,1));self.e.db.commit();before=self.e.reports(self.sid);cost=sum(x['cost'] or 0 for x in before['daily']);t=tariff_default();t['prices']=[2]*24;self.e.save_tariff(DEMO,{'space_id':self.sid,'tariff':t});r=self.e.reports(self.sid);self.assertAlmostEqual(r['total_kwh'],2);self.assertAlmostEqual(sum(x['cost'] or 0 for x in r['daily']),cost)
 def test_invalid_tariff_does_not_save(self):
  before=self.e.space(self.sid)['tariff'];t=tariff_default();t['prices'][0]=float('nan')
  with self.assertRaises(Error):self.e.dispatch('POST','/api/tariff',DEMO,{'space_id':self.sid,'tariff':t},{})
  self.assertEqual(self.e.space(self.sid)['tariff'],before)
 def test_budget_never_automatically_disconnects(self):
  t=tariff_default();t['monthly_budget']=1;self.e.save_tariff(DEMO,{'space_id':self.sid,'tariff':t});self.e.tick();self.assertTrue(self.e.device(self.d['id'])['relay_on']);self.assertFalse(self.e.snapshot(self.sid)['totals']['forecast_available'])
 def test_alert_ack_is_not_resolution(self):
  self.e.save_device(DEMO,{**self.d,'space_id':self.sid,'threshold_w':1});self.e.tick();self.e.check_alerts(clock());a=self.e.one("SELECT * FROM alerts WHERE key=?",('overload:'+self.d['id'],));self.assertEqual(a['state'],'active');self.e.dispatch('POST','/api/alert_ack',DEMO,{'id':a['id']},{});self.e.check_alerts(clock());self.assertEqual(self.e.one('SELECT state FROM alerts WHERE id=?',(a['id'],))['state'],'acknowledged');self.e.db.execute('UPDATE devices SET power_w=0 WHERE id=?',(self.d['id'],));now=clock();self.e.check_alerts(now);self.e.db.execute('UPDATE rule_state SET normal_since=? WHERE id=?',(now-11,'overload:'+self.d['id']));self.e.check_alerts(now);self.assertEqual(self.e.one('SELECT state FROM alerts WHERE id=?',(a['id'],))['state'],'resolved')
 def test_alert_deduplicated_while_active(self):
  for i in range(3):self.e.alert('same',self.sid,None,'warning','title','message',True,clock())
  self.assertEqual(len(self.e.rows('SELECT * FROM alerts WHERE key=?',('same',))),1)
 def test_auth_password_hashed_and_roles(self):
  password='Synthetic-password-12345';self.e.setup(DEMO,{'username':'owner','password':password});row=self.e.one('SELECT * FROM users');self.assertNotIn(password,row['password']);self.assertTrue(self.e.verify_password(password,row['password']));result,cookie=self.e.login({'username':'owner','password':password});self.assertEqual(result['user']['role'],'admin');self.e.save_user(ADMIN,{'username':'view','password':password,'role':'viewer'});self.assertEqual(len(self.e.rows('SELECT * FROM users')),2)
 def test_gateway_is_admin_only_and_token_hashed(self):
  with self.assertRaises(Error):self.e.create_gateway(DEMO,{'space_id':self.sid,'name':'gateway'})
  g=self.e.create_gateway(ADMIN,{'space_id':self.sid,'name':'gateway'});stored=self.e.one('SELECT * FROM gateways');self.assertNotEqual(stored['key_hash'],g['token']);self.assertEqual(self.e.gateway(g['token'])['id'],g['id'])
 def gateway_device(self):
  g=self.e.create_gateway(ADMIN,{'space_id':self.sid,'name':'gateway'});gateway=self.e.gateway(g['token']);d=self.e.save_device(ADMIN,{'space_id':self.sid,'name':'网关试验回路','area':'设备区','kind':'circuit','source':'gateway','gateway_id':g['id'],'nominal_w':1000,'threshold_w':2000});return gateway,d
 def test_gateway_sequence_duplicate_and_large_gap(self):
  g,d=self.gateway_device();now=clock();sample={'device_id':d['id'],'ts':now,'sequence':1,'power_w':600,'voltage':220,'pf':1,'relay_on':True};self.assertEqual(self.e.ingest(g,{'samples':[sample]})['accepted'],1);self.assertEqual(self.e.ingest(g,{'samples':[sample]})['accepted'],0);self.assertEqual(self.e.device(d['id'])['total_kwh'],0);sample={**sample,'sequence':2,'ts':now+2};self.e.ingest(g,{'samples':[sample]});self.assertAlmostEqual(self.e.device(d['id'])['total_kwh'],600*2/3600000)
 def test_gateway_get_is_not_execution_confirmation(self):
  g,d=self.gateway_device();now=clock();self.e.ingest(g,{'samples':[{'device_id':d['id'],'ts':now,'sequence':1,'power_w':500,'voltage':220,'relay_on':True}]});c=self.e.command(ADMIN,{'device_id':d['id'],'desired':False,'confirm_name':d['name'],'reason':'test','idempotency':uuid.uuid4().hex});leased=self.e.gateway_commands(g)['commands'];self.assertEqual(leased[0]['id'],c['id']);self.assertEqual(self.e.one('SELECT state FROM commands WHERE id=?',(c['id'],))['state'],'dispatched');self.assertTrue(self.e.device(d['id'])['relay_on']);ack=self.e.gateway_ack(g,{'command_id':c['id'],'success':True,'relay_on':False});self.assertEqual(ack['state'],'confirmed');self.assertFalse(self.e.device(d['id'])['relay_on'])
 def test_gateway_wrong_owner_rejected(self):
  g,d=self.gateway_device();other=self.e.create_gateway(ADMIN,{'space_id':self.sid,'name':'other'});g2=self.e.gateway(other['token'])
  with self.assertRaises(Error):self.e.ingest(g2,{'samples':[{'device_id':d['id'],'ts':clock(),'sequence':1,'power_w':500,'voltage':220,'relay_on':True}]})
 def test_invalid_batch_is_atomic(self):
  g,d=self.gateway_device();r={'device_id':d['id'],'ts':clock(),'sequence':1,'power_w':500,'voltage':220,'relay_on':True}
  with self.assertRaises(Error):self.e.ingest(g,{'samples':[r,{**r,'sequence':2,'relay_on':'yes'}]})
  self.assertEqual(self.e.device(d['id'])['sequence'],0)
 def test_rtsp_and_embedded_camera_password_rejected(self):
  for url in ['rtsp://camera/live','https://user:password@example.com/live']:
   with self.assertRaises(Error):self.e.save_camera(DEMO,{'space_id':self.sid,'name':'camera','area':'area','kind':'video','url':url})
 def test_camera_snapshot_saved(self):
  c=self.e.save_camera(DEMO,{'space_id':self.sid,'name':'camera','area':'area','kind':'snapshot','url':'https://example.com/camera.jpg'});self.assertEqual(c['kind'],'snapshot')
 def test_restart_preserves_audit_and_configuration(self):
  self.e.audit('test','marker','device','saved');self.e.db.commit();other=Energy(self.tmp.name,seed=False)
  try:self.assertEqual(other.space(self.sid)['name'],'任意用途空间');self.assertTrue(other.one("SELECT * FROM audit WHERE action='marker'"))
  finally:other.close()
 def test_schedule_for_protected_device_rejected(self):
  self.e.save_device(DEMO,{**self.d,'space_id':self.sid,'protected':True})
  with self.assertRaises(Error):self.e.save_schedule(DEMO,{'device_id':self.d['id'],'at':'22:00','desired':False})
 def test_partial_days_do_not_enable_month_forecast(self):
  midnight=local().replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
  for i in range(1,5):self.e.db.execute('INSERT INTO hourly VALUES(?,?,?,?,?)',(self.d['id'],int(midnight-i*86400),100,60,1))
  self.e.db.commit();totals=self.e.totals(self.sid,clock());self.assertEqual(totals['forecast_days'],0);self.assertFalse(totals['forecast_available'])
 def test_missing_heatmap_cells_are_not_zero_consumption(self):
  report=self.e.reports(self.sid);self.assertTrue(all(value is None for row in report['heat'] for value in row))
 def test_seed_is_one_generic_space_not_three_modes(self):
  other_dir=Path(self.tmp.name)/'seed';e=Energy(other_dir)
  try:self.assertEqual(len(e.spaces()),1);self.assertEqual(e.spaces()[0]['name'],'示范用电空间');e.tick();self.assertTrue(e.snapshot('space-demo')['totals']['forecast_available']);self.assertEqual(len(e.reports('space-demo')['heat']),7)
  finally:e.close()
@unittest.skipUnless(HTTP,'Use --http for loopback integration tests')
class HTTPTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup);self.e=Energy(self.tmp.name);self.addCleanup(self.e.close);self.server=Server(('127.0.0.1',0),self.e,ROOT/'web');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.shutdown);self.e.tick();self.cookie='';self.csrf='';status,j,headers=self.request('GET','/api/session');self.cookie=headers['Set-Cookie'].split(';')[0];self.csrf=j['csrf']
 def shutdown(self):self.e.stop.set();self.server.shutdown();self.server.server_close();self.thread.join(3)
 def request(self,method,path,body=None,headers=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8);h={'Cookie':self.cookie,'Content-Type':'application/json','X-CSRF-Token':self.csrf};h.update(headers or {});c.request(method,path,json.dumps(body) if body is not None else None,h);r=c.getresponse();raw=r.read();status=r.status;head=dict(r.getheaders());c.close()
  try:value=json.loads(raw)
  except Exception:value=raw
  return status,value,head
 def test_http_session_snapshot_and_static(self):
  status,s,_=self.request('GET','/api/snapshot?space_id=space-demo');self.assertEqual(status,200);self.assertEqual(s['device_count'],6);status,html,headers=self.request('GET','/');self.assertEqual(status,200);self.assertIn(b'<html',html);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
 def test_csrf_and_cross_site_rejected(self):
  status,_,_=self.request('POST','/api/spaces',{'name':'blocked'},{'X-CSRF-Token':'wrong'});self.assertEqual(status,403);status,_,_=self.request('GET','/api/snapshot',headers={'Origin':'https://example.com'});self.assertEqual(status,403)
 def test_host_and_path_traversal_rejected(self):
  status,_,_=self.request('GET','/',headers={'Host':'attacker.example'});self.assertEqual(status,403);status,_,_=self.request('GET','/%2e%2e/backend/server.py');self.assertEqual(status,404)
 def test_export_is_real_csv(self):
  status,csv,h=self.request('GET','/api/export?space_id=space-demo');self.assertEqual(status,200);self.assertTrue(csv.startswith(b'\xef\xbb\xbf'));self.assertIn('text/csv',h['Content-Type'])
 def test_get_cannot_control_device(self):
  status,_,_=self.request('GET','/api/command?device_id=air');self.assertEqual(status,404)
 def test_sse_real_frames(self):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8);c.request('GET','/api/stream?space_id=space-demo',headers={'Cookie':self.cookie});r=c.getresponse();self.assertEqual(r.status,200);self.assertIn('text/event-stream',r.getheader('Content-Type'));lines=[]
  for _ in range(7):
   line=r.fp.readline().decode();lines.append(line)
   if line.startswith('data: '):break
  data=next(x for x in lines if x.startswith('data: '));self.assertEqual(json.loads(data[6:])['source'],'simulated');c.close()
if __name__=='__main__':unittest.main(verbosity=2)
