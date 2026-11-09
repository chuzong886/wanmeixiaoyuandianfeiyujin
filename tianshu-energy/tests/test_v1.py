"""v1 regressions; isolated synthetic databases, no physical devices or outbound delivery."""
import sys,tempfile,unittest,json,uuid,base64,sqlite3,time,concurrent.futures
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from energy import Energy,Error,clock,tariff_default,local
ADMIN={'id':'qa','username':'qa','role':'admin'}

class V1(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.e=Energy(self.tmp.name,seed=False);self.addCleanup(self.e.close);self.s=self.e.save_space(ADMIN,{'name':'合成验证空间'});self.sid=self.s['id']
 def add(self,**extra):
  return self.e.save_device(ADMIN,{'space_id':self.sid,'name':'测试设备','area':'测试区','device_type':'socket','source':'simulated','nominal_w':1000,'threshold_w':1500,**extra})
 def installed(self,d):
  self.e.db.execute('UPDATE devices SET created_at=0 WHERE id=?',(d['id'],));self.e.db.execute('UPDATE scopes SET effective=0 WHERE device_id=?',(d['id'],));self.e.db.commit()
 def test_readonly_has_no_switch(self):
  d=self.add(device_type='readonly_meter');self.e.tick();view=self.e.devices(self.sid)[0];self.assertFalse(view['capabilities']['switch']);self.assertIsNone(view['relay_on'])
  with self.assertRaises(Error):self.e.command(ADMIN,{'device_id':d['id'],'desired':False})
 def test_live_trend_retains_more_than_three_minutes(self):
  self.add();start=clock()
  for i in range(200):self.e.tick(start+i)
  trend=self.e.snapshot(self.sid)['trend'];self.assertEqual(len(trend),200);self.assertGreater(trend[-1]['ts']-trend[0]['ts'],180);self.assertEqual(self.e.streams[self.sid].maxlen,1800)
 def test_plain_appliance_has_no_fake_measurements(self):
  self.add(device_type='appliance');self.e.tick();d=self.e.devices(self.sid)[0];self.assertFalse(d['has_power']);self.assertIsNone(d['power_w']);self.assertEqual(d['switch_state'],'unknown')
 def test_topology_deduplicates_parent_child(self):
  p=self.add(device_type='readonly_meter');d=self.add(meter_parent_id=p['id']);self.assertEqual(self.e._selected(clock()),{p['id']})
  with self.assertRaises(Error):self.e.save_device(ADMIN,{**p,'meter_parent_id':d['id']})
 def test_scope_changes_preserve_history(self):
  d=self.add();self.installed(d);now=clock();self.e.save_device(ADMIN,{**d,'counted':False});self.assertIn(d['id'],self.e._selected(now-60));self.assertNotIn(d['id'],self.e._selected(clock()))
 def test_future_tariff_not_active_early(self):
  old=self.e.space(self.sid)['tariff'];t={**tariff_default(),'mode':'fixed','fixed_price':9};self.e.save_tariff(ADMIN,{'space_id':self.sid,'tariff':t,'effective_at':clock()+3600});self.assertEqual(self.e.space(self.sid)['tariff'],old);self.assertEqual(self.e.price_at(self.sid,clock()+7200)['prices'][0],9)
 def test_restart_does_not_activate_future_scope(self):
  d=self.add(effective_at=clock()+3600);self.assertNotIn(d['id'],self.e._selected(clock()));other=Energy(self.tmp.name,seed=False)
  try:self.assertNotIn(d['id'],other._selected(clock()))
  finally:other.close()
 def test_partial_coverage_requires_every_selected_meter(self):
  a=self.add();b=self.add();self.installed(a);self.installed(b);h=int(clock()//3600)*3600-3600;self.e.db.execute('INSERT INTO hourly VALUES(?,?,?,?,?)',(a['id'],h,1000,3600,0));self.assertEqual(self.e._energy_rows(self.sid,h,h+3600)[0]['coverage_s'],0)
 def test_empty_space_not_made_complete_by_other_space(self):
  self.add();s=self.e.save_space(ADMIN,{'name':'空空间'});self.assertFalse(self.e.snapshot(s['id'])['power_complete'])
 def test_tariff_cannot_rewrite_past(self):
  before=self.e.price_at(self.sid,clock()-3600);self.e.save_tariff(ADMIN,{'space_id':self.sid,'tariff':{**tariff_default(),'mode':'fixed','fixed_price':9}});self.assertEqual(self.e.price_at(self.sid,clock()-3600),before)
 def test_missing_is_not_zero_and_has_no_peak(self):
  self.add();r=self.e.reports(self.sid);self.assertTrue(all(x['kwh'] is None for x in r['daily']));self.assertEqual(r['peak_hours'],[]);self.assertIsNone(self.e.totals(self.sid,clock())['month_cost'])
 def test_rule_duration_and_recovery_are_distinct(self):
  now=clock();args=('test',self.sid,None,'warning','测试');self.e.evaluate(*args,True,now,120,30);self.assertIsNone(self.e.one('SELECT * FROM alerts'));self.e.evaluate(*args,True,now+120,120,30);a=self.e.one('SELECT * FROM alerts');self.assertEqual(a['machine_state'],'active');self.e.evaluate(*args,False,now+121,120,30);self.assertEqual(self.e.one('SELECT * FROM alerts')['machine_state'],'active');self.e.evaluate(*args,False,now+151,120,30);self.assertEqual(self.e.one('SELECT * FROM alerts')['machine_state'],'normal');self.assertEqual(self.e.one('SELECT * FROM alerts')['handling_state'],'pending')
 def test_offline_does_not_resolve_existing_alarm(self):
  now=clock();self.e.alert('test',self.sid,None,'critical','异常','',True,now);self.e.evaluate('test',self.sid,None,'critical','异常',None,now+1);a=self.e.one('SELECT * FROM alerts');self.assertEqual(a['machine_state'],'unknown');self.assertEqual(a['state'],'active')
 def test_custom_power_rule_not_bypassed_by_builtin(self):
  d=self.add(threshold_w=1);self.e.dispatch('POST','/api/rules',ADMIN,{'space_id':self.sid,'name':'持续两分钟','config':{'metric':'power','device_id':d['id'],'threshold':1,'duration':120}},{});self.e.tick();self.assertIsNone(self.e.one('SELECT * FROM alerts'))
 def test_communication_uses_measurement_time_not_reception(self):
  d=self.add();self.e.db.execute('UPDATE devices SET last_seen=?,last_sample=? WHERE id=?',(clock(),clock()-110,d['id']));self.assertFalse(self.e.devices(self.sid)[0]['online'])
 def test_protected_descendant_blocks_upstream_cutoff(self):
  p=self.add(device_type='breaker');self.add(controller_id=p['id'],protected=True);self.e.tick()
  with self.assertRaises(Error):self.e.command(ADMIN,{'device_id':p['id'],'desired':False,'confirm_name':p['name'],'reason':'测试','idempotency':uuid.uuid4().hex})
 def test_noop_schedule_does_not_stop_collector(self):
  d=self.add();self.e.tick();self.e.save_schedule(ADMIN,{'device_id':d['id'],'at':local(clock()).strftime('%H:%M'),'desired':True});self.e.tick();self.assertTrue(self.e.last_tick);self.assertIn('跳过',self.e.one('SELECT * FROM schedules')['last_result'])
 def test_gateway_switch_device_accepts_schedule(self):
  created=self.e.create_gateway(ADMIN,{'space_id':self.sid,'name':'计划测试网关'});gateway=self.e.gateway(created['token']);d=self.add(source='gateway',gateway_id=gateway['id']);result=self.e.save_schedule(ADMIN,{'device_id':d['id'],'at':'22:00','desired':False,'weekdays':[0,1,2,3,4]});self.assertEqual(self.e.one('SELECT device_id FROM schedules WHERE id=?',(result['id'],))['device_id'],d['id'])
 def test_scoped_permissions(self):
  user={'id':'viewer','role':'viewer','username':'测试'}
  with self.assertRaises(Error):self.e.dispatch('GET','/api/snapshot',user,None,{'space_id':self.sid})
  self.e.db.execute('INSERT INTO space_members VALUES(?,?)',(self.sid,user['id']));self.e.access(user,self.sid)
  with self.assertRaises(Error):self.e.access(user,self.sid,True)
 def test_balance_and_cumulative_not_double_counted(self):
  c={'url':'http://127.0.0.1:9','method':'GET','mapping':{'balance_kwh':'balance','cumulative_kwh':'energy'},'enabled':False};d=self.add(device_type='readonly_meter',source='third_party',api_config=c);self.installed(d);row=self.e.one('SELECT * FROM api_devices WHERE device_id=?',(d['id'],));config=self.e.api.config(d['id']);now=clock()
  self.e.api.accept(d,row,config,{'balance_kwh':10,'cumulative_kwh':100},now-60);self.e.api.accept(d,row,config,{'balance_kwh':10,'cumulative_kwh':101},now);self.assertAlmostEqual(sum(x['energy_wh'] for x in self.e.rows('SELECT * FROM hourly')),1000)
 def test_short_balance_intervals_and_debt(self):
  c={'url':'http://127.0.0.1:9','mapping':{'balance_kwh':'balance'},'enabled':False};d=self.add(device_type='readonly_meter',source='third_party',api_config=c);self.installed(d);row=self.e.one('SELECT * FROM api_devices WHERE device_id=?',(d['id'],));config=self.e.api.config(d['id']);now=clock()
  for gap,b in [(60,1),(30,.5),(0,-.5)]:self.e.api.accept(d,row,config,{'balance_kwh':b},now-gap)
  self.assertAlmostEqual(sum(x['energy_wh'] for x in self.e.rows('SELECT * FROM hourly')),1500);self.assertEqual(self.e.api.normalize(config,{'balance':-2})['balance_kwh'],-2)
 def test_backup_restore_roundtrip_isolated(self):
  self.add();copy=sqlite3.connect(':memory:');self.e.db.backup(copy);raw=copy.serialize();copy.close();self.add(name='备份后新增');self.e.dispatch('POST','/api/restore',ADMIN,{'confirmation':'恢复备份','data':base64.b64encode(raw).decode()},{});self.assertEqual(len(self.e.devices(self.sid)),1)
 def test_factory_reset_requires_password_and_lists_every_table(self):
  self.add();password='Synthetic-reset-test-123';self.e.setup(ADMIN,{'username':'qa-reset','password':password});user={**ADMIN,'id':self.e.one('SELECT id FROM users')['id']};preview=self.e.dispatch('POST','/api/reset-preview',user,{},{});self.assertIn('audit',preview['records']);self.assertIn('spaces',preview['records'])
  with self.assertRaises(Error):self.e.dispatch('POST','/api/factory-reset',user,{'token':preview['token'],'confirmation':'恢复出厂设置','password':'wrong'},{})
  self.assertEqual(len(self.e.devices(self.sid)),1);preview=self.e.dispatch('POST','/api/reset-preview',user,{},{});self.e.dispatch('POST','/api/factory-reset',user,{'token':preview['token'],'confirmation':'恢复出厂设置','password':password},{});self.assertEqual(self.e.rows('SELECT * FROM devices'),[]);self.assertEqual(len(self.e.spaces()),1)
 def test_notification_retry_and_secret_redaction(self):
  ch=self.e.dispatch('POST','/api/channels',ADMIN,{'name':'合成渠道','kind':'smtp','config':{'host':'smtp.example.invalid','sender':'qa@example.invalid','recipients':'test@example.invalid','password':'Synthetic-secret'}},{})
  public=self.e.dispatch('GET','/api/channel-config',ADMIN,{}, {'id':ch['id']});self.assertNotIn('password',public['channel']['config'])
  with patch.object(self.e,'send_channel',side_effect=Error('合成连接失败')):
   self.e.dispatch('POST','/api/channel-test',ADMIN,{'id':ch['id']},{})
   for _ in range(100):
    if not self.e.delivery_busy:break
    time.sleep(.01)
  row=self.e.one('SELECT * FROM deliveries');self.assertEqual(row['state'],'retry');self.assertEqual(row['attempts'],1);self.assertNotIn('Synthetic-secret',row['error'])
 def test_feishu_payload_and_secret_redaction(self):
  body=self.e.webhook_body('feishu',{'secret':'Synthetic-signing-secret'},{'event_id':'evt-1','title':'功率超限','message':'设备超过阈值','level':'critical'},1700000000);self.assertEqual(body['msg_type'],'text');self.assertIn('功率超限',body['content']['text']);self.assertEqual(body['timestamp'],'1700000000');self.assertTrue(body['sign'])
  saved=self.e.dispatch('POST','/api/channels',ADMIN,{'name':'飞书测试','kind':'feishu','enabled':True,'config':{'url':'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic','secret':'Synthetic-signing-secret'}},{});public=self.e.dispatch('GET','/api/channel-config',ADMIN,{}, {'id':saved['id']});self.assertNotIn('secret',public['channel']['config'])
 def test_pagination_and_capability_validation(self):
  for i in range(23):self.e.alert(str(i),self.sid,None,'warning','测试','测试',True,clock()+i)
  result=self.e.dispatch('GET','/api/alerts',ADMIN,{}, {'space_id':self.sid,'page':'2','size':'20'});self.assertEqual(result['total'],23);self.assertEqual(len(result['alerts']),3)
  d=self.add(device_type='appliance')
  with self.assertRaises(Error):self.e.dispatch('POST','/api/rules',ADMIN,{'space_id':self.sid,'name':'无效功率','config':{'metric':'power','device_id':d['id']}},{})
 def test_empty_api_mappings_cannot_invent_power_or_cumulative(self):
  from thirdparty import validate
  c=validate({'url':'http://127.0.0.1:9','mapping':{'balance_kwh':'balance','cumulative_kwh':'','power_w':'','voltage':''},'scale':2});self.assertEqual(self.e.api.normalize(c,{'balance':12}),{'balance_kwh':24})
 def test_gateway_readonly_meter_needs_no_relay_and_applies_scale(self):
  token=self.e.create_gateway(ADMIN,{'space_id':self.sid,'name':'合成网关'});g=self.e.gateway(token['token']);d=self.add(device_type='readonly_meter',source='gateway',gateway_id=g['id'],scale=2);now=clock()
  for sequence,ts in [(1,now-2),(2,now)]:self.e.ingest(g,{'samples':[{'device_id':d['id'],'sequence':sequence,'ts':ts,'power_w':100,'voltage':220}]})
  view=self.e.devices(self.sid)[0];self.assertTrue(view['online']);self.assertEqual(view['power_w'],200);self.assertEqual(view['voltage'],220);self.assertIsNone(view['relay_on']);self.assertAlmostEqual(view['total_kwh'],400/3600000)
 def test_collector_and_dashboard_serialize_shared_connection(self):
  self.add();self.add(name='并发测试2')
  def work(index):
   for _ in range(50):
    if index==0:self.e.tick()
    elif index==1:self.e.reports(self.sid)
    else:self.e.snapshot(self.sid)
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(work,range(4)))
  self.assertIsNone(self.e.last_error);self.assertIsNotNone(self.e.last_tick)

if __name__=='__main__':unittest.main()
