"""Synthetic API devices only. No campus credentials, actual API calls, or control requests."""
import sys,json,unittest,tempfile,time,sqlite3,threading,http.client
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy,Error,SCHEMA,clock
from thirdparty import validate,path_value
BASE=ROOT/'build/api-tests';BASE.mkdir(parents=True,exist_ok=True)
ADMIN={'username':'synthetic-owner','role':'admin'};VIEWER={'username':'viewer','role':'viewer'}
def config(**extra):return {'url':'http://127.0.0.1:9/query','method':'POST','encoding':'json','headers':{'Authorization':'Bearer synthetic-secret-only'},'body':{'meterId':'synthetic-1'},'mapping':{'balance_kwh':'data.balance'},'interval':3600,'enabled':False,**extra}
class Devices(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.tmp.cleanup);self.e=Energy(self.tmp.name,seed=False);self.addCleanup(self.e.close);self.s=self.e.save_space(ADMIN,{'name':'任意用电环境'});self.d=self.add()
 def add(self,**extra):return self.e.save_device(ADMIN,{'space_id':self.s['id'],'name':'第三方测试电表','area':'自定义区域','kind':'meter','source':'third_party','nominal_w':1000,'threshold_w':2000,'api_config':config(),**extra})
 def row(self):return self.e.one('SELECT * FROM api_devices WHERE device_id=?',(self.d['id'],))
 def accept(self,balance,ts):
  self.e.api.accept(self.d,self.row(),self.e.api.config(self.d['id']),{'balance_kwh':balance},ts);self.e.db.commit()
 def test_real_source_is_not_simulated_and_no_implicit_request(self):
  self.assertEqual(self.d['source'],'third_party');self.assertFalse(self.d['controllable']);self.assertEqual(self.e.rows('SELECT * FROM api_attempts'),[]);self.e.tick();self.assertEqual(self.e.rows('SELECT * FROM api_readings'),[])
 def test_credentials_encrypted_and_snapshot_redacted(self):
  raw=self.row()['config'];self.assertNotIn(b'synthetic-secret-only',raw);self.assertEqual(self.e.api.config(self.d['id'])['headers']['Authorization'],'Bearer synthetic-secret-only');self.assertNotIn('synthetic-secret-only',json.dumps(self.e.snapshot(self.s['id'])));self.assertNotIn('meterId',json.dumps(self.e.snapshot(self.s['id'])))
 def test_viewer_cannot_add_or_query(self):
  with self.assertRaises(Error):self.e.api.query(VIEWER,self.d['id'])
  with self.assertRaises(Error):self.e.save_device(VIEWER,{'source':'third_party'})
 def test_unreadable_api_credentials_do_not_break_entire_dashboard(self):
  def unavailable(did):raise Error('unreadable')
  self.e.api.config=unavailable;self.e.tick();s=self.e.snapshot(self.s['id']);self.assertTrue(s['devices'][0]['api']['configuration_error']);self.assertFalse(s['power_available']);self.assertIsNotNone(self.e.one('SELECT id FROM alerts'))
 def test_balance_is_not_current_power(self):
  self.accept(23.5,clock());s=self.e.snapshot(self.s['id']);d=s['devices'][0];self.assertFalse(d['has_power']);self.assertFalse(s['power_available']);self.assertIsNone(d['relay_on']);self.assertEqual(d['api']['reading']['balance'],23.5);self.assertEqual(d['total_kwh'],0)
 def test_cumulative_minute_samples_are_counted_and_reset_not_negative_use(self):
  now=clock();c=self.e.api.config(self.d['id']);row=self.row()
  for ts,value in [(now-120,10),(now-60,10.5),(now,0)]:self.e.api.accept(self.d,row,c,{'cumulative_kwh':value},ts)
  self.assertAlmostEqual(sum(r['energy_wh'] for r in self.e.rows('SELECT * FROM hourly')),500);self.assertEqual(self.e.rows('SELECT quality FROM api_readings ORDER BY ts')[-1]['quality'],'recharge_or_reset')
 def test_balance_drop_enters_same_space_reports_and_alerts(self):
  self.e.db.execute('UPDATE devices SET created_at=0 WHERE id=?',(self.d['id'],));self.e.db.execute('UPDATE scopes SET effective=0 WHERE device_id=?',(self.d['id'],));now=clock();self.accept(10,now-7200);self.accept(2,now);r=self.e.reports(self.s['id']);self.assertAlmostEqual(r['total_kwh'],8);self.e.check_alerts(now);self.assertEqual(self.e.one("SELECT title FROM alerts WHERE key=?",('api-critical:'+self.d['id'],))['title'],'剩余电量紧急')
 def test_plateau_keeps_original_anchor_and_recharge_not_consumption(self):
  now=clock();self.accept(50,now-6*3600);self.accept(50,now-2*3600);self.accept(44,now);rows=self.e.rows('SELECT * FROM hourly');self.assertAlmostEqual(sum(r['energy_wh'] for r in rows),6000);self.assertAlmostEqual(sum(r['coverage_s'] for r in rows),6*3600);self.accept(80,now+3600);self.assertAlmostEqual(sum(r['energy_wh'] for r in self.e.rows('SELECT * FROM hourly')),6000)
 def test_infinite_missing_boolean_numbers_rejected(self):
  c=validate(config())
  for v in [None,True,'',float('inf')]:
   with self.assertRaises(Error):self.e.api.normalize(c,{'data':{'balance':v}})
  with self.assertRaises(Error):self.e.api.normalize(c,{'data':{}})
  self.assertEqual(self.e.api.normalize(c,{'data':{'balance':-1}})['balance_kwh'],-1)
 def test_generic_json_array_mapping_and_business_failure(self):
  self.assertEqual(path_value({'data':[{'power':1200}]},'data.0.power'),1200);c=validate(config(success_path='code',success_value='0'))
  with self.assertRaises(Error):self.e.api.normalize(c,{'code':1,'data':{'balance':100}})
 def test_failed_mapping_never_saves_reading_or_erases_previous(self):
  self.accept(8,clock()-7200);self.e.api.transport=lambda c,p:{'data':{'balance':None}}
  with self.assertRaises(Error):self.e.api.query(ADMIN,self.d['id'])
  self.assertEqual(len(self.e.rows('SELECT * FROM api_readings')),1);self.assertEqual(self.row()['failures'],1)
 def test_query_uses_protocol_and_forecast_without_external_network(self):
  self.e.api.transport=lambda c,p:{'data':{'balance':15.6}}
  result=self.e.api.query(ADMIN,self.d['id']);self.assertTrue(result['ok']);self.assertEqual(self.e.rows('SELECT balance FROM api_readings')[0]['balance'],15.6);self.assertIn('analysis',json.loads(self.row()['forecast']))
  with self.assertRaises(Error):self.e.api.query(ADMIN,self.d['id'])
 def test_cannot_change_meter_identity_over_existing_readings(self):
  self.accept(18,clock());before=self.row()['series']
  with self.assertRaises(Error):self.e.save_device(ADMIN,{**self.d,'name':'changed','api_config':config(body={'meterId':'another'})})
  self.assertEqual(self.row()['series'],before);self.assertEqual(self.e.device(self.d['id'])['name'],'第三方测试电表');self.assertEqual(len(self.e.rows('SELECT * FROM api_readings')),1)
 def test_existing_forecast_and_encryption_helpers_are_real(self):
  c=validate(config(adapter='wanxiao',school_code='TST',account='ACC'))
  prepared=self.e.api.adapter('prepare',config=c);self.assertIn('encryptData',prepared);self.assertNotIn('TEST-ACCOUNT',prepared['encryptData'])
  response={'result_':'true','code_':'0','body':{'result':'0','roomlist':[{'roomverify':'A','roomfullname':'合成宿舍','detaillist':[{'businesstype':'0','odd':'12.30'}]}]}}
  self.assertEqual(self.e.api.normalize(c,response)['balance_kwh'],12.3)
  response['body']['roomlist'].append({'roomverify':'B','detaillist':[{'businesstype':'0','odd':'2'}]});self.assertTrue(self.e.api.normalize(c,response)['selection_required'])
 def test_original_forecast_requires_samples_and_handles_recharge(self):
  now=clock();h=[{'ts':now-(3-i)*4*3600,'balance':30-i*4,'balance_raw':str(30-i*4)} for i in range(4)]
  result=self.e.api.adapter('forecast',history=h,options={});self.assertTrue(result['forecast']['available']);self.assertIn('conservative',result['analysis']['algorithm_version']);h.append({'ts':now+3600,'balance':100,'balance_raw':'100'});self.assertFalse(self.e.api.adapter('forecast',history=h,options={})['forecast']['available'])
 def test_headers_and_url_validation(self):
  for c in [config(url='https://name:password@example.com/api'),config(headers={'Host':'evil'}),config(headers={'Authorization':'a\r\nb'}),config(url='http://remote.example/api')]:
   with self.assertRaises(Error):validate(c)
 def test_capture_keeps_multiple_candidates_for_explicit_selection(self):
  r=self.e.api.adapter('capture',source=json.dumps([{'school_code':'A','account':'A'},{'school_code':'B','account':'B'}]));self.assertEqual(len(r['candidates']),2);self.assertEqual({c['account'] for c in r['candidates']},{'A','B'})
 def test_stale_reading_does_not_resolve_existing_balance_alert(self):
  self.accept(2,clock());self.e.check_alerts(clock());key='api-critical:'+self.d['id'];self.assertEqual(self.e.one('SELECT state FROM alerts WHERE key=?',(key,))['state'],'active');self.e.db.execute('UPDATE api_devices SET failures=1 WHERE device_id=?',(self.d['id'],));self.e.check_alerts(clock());self.assertEqual(self.e.one('SELECT state FROM alerts WHERE key=?',(key,))['state'],'active')
 def test_enabled_schedule_really_queries_in_background(self):
  self.e.api.transport=lambda c,p:{'data':{'balance':12.5}};self.e.api.save(self.d['id'],config(enabled=True));self.e.db.execute('UPDATE api_devices SET next_poll=0 WHERE device_id=?',(self.d['id'],));self.e.db.commit();self.e.api.schedule();deadline=time.monotonic()+8
  while time.monotonic()<deadline:
   with self.e.lock:done=self.e.one("SELECT id FROM api_attempts WHERE state='ok'") is not None;busy=self.d['id'] in self.e.api.inflight
   if done and not busy:break
   time.sleep(.05)
  self.assertTrue(done);self.assertFalse(busy);self.assertEqual(self.e.rows('SELECT balance FROM api_readings')[0]['balance'],12.5)

class Migration(unittest.TestCase):
 def test_010_database_upgrades_without_losing_devices_history(self):
  with tempfile.TemporaryDirectory(dir=BASE) as directory:
   db=sqlite3.connect(Path(directory)/'energy.sqlite');db.executescript(SCHEMA.replace("'simulated','gateway','third_party'","'simulated','gateway'"));db.execute("INSERT INTO spaces VALUES('old','原空间','','{}',0)");db.execute("INSERT INTO devices(id,space_id,name,area,kind,source,nominal_w,threshold_w) VALUES('old-device','old','原设备','原区域','circuit','simulated',1000,2000)");db.execute("INSERT INTO hourly VALUES('old-device',1000,1200,3600,1)");db.commit();db.close();e=Energy(directory,seed=False)
   try:self.assertEqual(e.device('old-device')['name'],'原设备');self.assertEqual(e.rows('SELECT * FROM hourly')[0]['energy_wh'],1200);self.assertIn("'third_party'",e.one("SELECT sql FROM sqlite_master WHERE name='devices'")['sql'])
   finally:e.close()

if __name__=='__main__':unittest.main(verbosity=2)
