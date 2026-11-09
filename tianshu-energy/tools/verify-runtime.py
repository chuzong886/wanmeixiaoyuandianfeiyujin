"""Verify the staged Windows payload in isolation, with a synthetic local database."""
from pathlib import Path
import subprocess,json,http.client,uuid,time,re
root=Path(__file__).resolve().parents[1];app=root/'dist/windows-1.0.0';data=root/'build'/('payload-test-'+uuid.uuid4().hex);data.mkdir(parents=True)
adapter_check='''
import sys,json
from energy import Energy
e=Energy(sys.argv[1],seed=False)
try:
 user={'username':'synthetic-test','role':'admin'};s=e.save_space(user,{'name':'合成验收空间'})
 c={'adapter':'wanxiao','url':'https://example.invalid/query','school_code':'TST','account':'ACC','enabled':False,'headers':{'Authorization':'synthetic-only'}}
 d=e.save_device(user,{'space_id':s['id'],'name':'协议验收','area':'合成区域','kind':'meter','source':'third_party','nominal_w':1000,'threshold_w':2000,'api_config':c})
 assert e.api.config(d['id'])['account']=='ACC'
 assert b'synthetic-only' not in e.one('SELECT config FROM api_devices')['config']
 assert 'encryptData' in e.api.adapter('prepare',config=c)
 assert not e.snapshot(s['id'])['power_available']
 print('PASS: bundled DPAPI credential round-trip, real protocol adapter, source integration and unknown power')
finally:e.close()
'''
subprocess.run([str(app/'runtime/python.exe'),'-c',adapter_check,str(data/'adapter-check')],check=True)
process=subprocess.Popen([str(app/'runtime/python.exe'),'-u',str(app/'backend/server.py'),'--parent-pipe','--data-dir',str(data),'--web-dir',str(app/'web'),'--port','0'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
try:
 ready=json.loads(process.stdout.readline());port=int(ready['url'].rsplit(':',1)[1]);connection=http.client.HTTPConnection('127.0.0.1',port,timeout=15);connection.request('GET','/api/session');response=connection.getresponse();session=json.loads(response.read());cookie=response.getheader('Set-Cookie').split(';')[0];assert session['user']['role']=='demo';connection.close()
 connection=http.client.HTTPConnection('127.0.0.1',port,timeout=15);connection.request('GET','/api/snapshot?space_id=space-demo',headers={'Cookie':cookie});response=connection.getresponse();snapshot=json.loads(response.read());assert response.status==200;assert snapshot['device_count']==6;assert snapshot['source']=='simulated';assert time.time()-snapshot['heartbeat']<5;connection.close()
 connection=http.client.HTTPConnection('127.0.0.1',port,timeout=15);connection.request('GET','/');response=connection.getresponse();html=response.read().decode();assert response.status==200;connection.close()
 for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"',html):
  connection=http.client.HTTPConnection('127.0.0.1',port,timeout=15);connection.request('GET',asset);response=connection.getresponse();raw=response.read();assert response.status==200 and len(raw)>100;connection.close()
 print('PASS: bundled Python, SQLite, fresh one-second collection, demo session, snapshot and current web assets')
finally:
 process.stdin.close()
 try:code=process.wait(timeout=12)
 except subprocess.TimeoutExpired:process.kill();raise RuntimeError('Packaged server did not stop on parent EOF')
 if code!=0:raise RuntimeError('Packaged server exited with error')
 print('PASS: owned backend stops when launcher closes its parent pipe')
