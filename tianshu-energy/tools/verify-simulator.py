"""Verify the staged simulator's embedded runtime and optional renamed Windows apphost."""
from pathlib import Path
import subprocess,json,http.client,uuid,time,sys,socket
root=Path(__file__).resolve().parents[1];app=root/'dist/simulator-1.0.1';data=root/'build'/('simulator-payload-'+uuid.uuid4().hex);data.mkdir(parents=True)
def call(port,path,body=None,cookie='',csrf='',key=''):
 c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
 try:
  c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),{'Content-Type':'application/json','Cookie':cookie,'X-CSRF-Token':csrf,**({'Authorization':'Bearer '+key} if key else {})});r=c.getresponse();raw=r.read();return r.status,raw,r.getheader('Set-Cookie')
 finally:c.close()
process=subprocess.Popen([str(app/'runtime/python.exe'),'-u',str(app/'backend/simulator.py'),'--parent-pipe','--data-dir',str(data),'--web-dir',str(app/'web'),'--port','0'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
try:
 ready=json.loads(process.stdout.readline());assert ready['simulation'];port=int(ready['url'].rsplit(':',1)[1]);status,raw,_=call(port,'/api/health');assert status==200 and json.loads(raw)['simulation'];status,raw,_=call(port,'/');assert status==200 and 'simulator-' in raw.decode();status,raw,cookie=call(port,'/sim/session');csrf=json.loads(raw)['csrf'];cookie=cookie.split(';')[0];status,raw,_=call(port,'/sim/state',cookie=cookie);state=json.loads(raw);assert state['simulation'] and not state['gateways'];mid=state['meters'][0]['id'];status,raw,_=call(port,'/sim/credentials',{},cookie,csrf);assert status==200;key=json.loads(raw)['api_key'];status,raw,_=call(port,'/mock/meters/'+mid,key=key);assert status==200 and json.loads(raw)['simulation'];assert call(port,'/mock/meters/'+mid)[0]==401;status,_,_=call(port,'/sim/meter-action',{'id':mid,'action':'balance','amount':-5},cookie,csrf);assert status==200;status,raw,_=call(port,'/mock/meters/'+mid,key=key);assert json.loads(raw)['data']['balance_kwh']<0
 status,raw,headers=call(port,'/mock/camera/snapshot.jpg');assert status==200 and raw.startswith(b'\xff\xd8') and headers is None
 for index in range(1,5):
  status,raw,_=call(port,'/mock/camera/frames/'+str(index)+'.jpg');assert status==200 and raw.startswith(b'\xff\xd8')
 print('PASS: bundled runtime, DPAPI API token, simulator HTML, local HTTP API auth, negative balance, no enabled gateways, four JPEG camera photos')
finally:
 process.stdin.close()
 try:code=process.wait(timeout=15)
 except subprocess.TimeoutExpired:process.kill();raise RuntimeError('Simulator did not stop on parent EOF')
 assert code==0
 print('PASS: simulator stops on parent EOF')
if '--launcher' in sys.argv:
 # Use a fresh test port; never stop an existing simulator on8792.
 with socket.socket() as reservation:reservation.bind(('127.0.0.1',0));test_port=reservation.getsockname()[1]
 startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
 launcher=subprocess.Popen([str(app/'Tianshu.Simulator.exe'),'--no-browser','--no-window','--port',str(test_port),'--data-dir',str(data/'launcher')],startupinfo=startup)
 try:
  started=False
  for _ in range(40):
   if launcher.poll() is not None:raise RuntimeError('Simulator launcher exited early')
   try:
    status,raw,_=call(test_port,'/api/health');started=status==200 and json.loads(raw).get('simulation') is True
    if started:break
   except OSError:pass
   time.sleep(.25)
  assert started,'Renamed simulator apphost did not start simulator backend'
  assert (data/'launcher/simulator.sqlite').is_file() and not (data/'launcher/energy.sqlite').exists()
  print('PASS: renamed Windows EXE starts simulator.py with its isolated simulator database, not the main energy backend')
 finally:
  launcher.terminate();launcher.wait(timeout=10)
  closed=False
  for _ in range(40):
   try:call(test_port,'/api/health')
   except OSError:closed=True;break
   time.sleep(.25)
  assert closed,'Owned child simulator did not exit with launcher'
  print('PASS: owned simulator backend exits after launcher termination')
