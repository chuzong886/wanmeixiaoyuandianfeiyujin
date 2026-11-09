"""Synthetic photo/HTTP tests. No real camera, external network, or user photos."""
import sys,unittest,tempfile,threading,time,json,http.client,base64,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from simulator import Simulator,Server
from simulator_camera import CameraFeed,jpeg_size
from energy import Energy,Error
BASE=ROOT/'build/simulator-camera-tests';BASE.mkdir(parents=True,exist_ok=True)
class Camera(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=BASE);self.app=Simulator(self.tmp.name);self.server=Server(('127.0.0.1',0),self.app,ROOT/'frontend/public');self.feed=self.server.camera;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):self.app.stop.set();self.server.shutdown();self.server.server_close();self.thread.join(3);self.app.close();self.tmp.cleanup()
 def call(self,path,body=None,headers=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
  try:
   c.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),{'Content-Type':'application/json',**(headers or {})});r=c.getresponse();return r.status,r.read(),dict(r.getheaders())
  finally:c.close()
 def auth(self):
  _,raw,headers=self.call('/sim/session');return {'Cookie':headers['Set-Cookie'].split(';')[0],'X-CSRF-Token':json.loads(raw)['csrf']}
 def test_four_distinct_valid_jpegs(self):
  hashes=[]
  for i in range(1,5):
   index,raw=self.feed.frame(i);self.assertEqual(index,i);width,height=jpeg_size(raw);self.assertGreater(width,height);hashes.append(hashlib.sha256(raw).hexdigest())
  self.assertEqual(len(set(hashes)),4);self.assertEqual(self.feed.status()['interval_s'],5);self.assertTrue(self.feed.status()['simulation'])
 def test_order_is_one_two_three_four_one(self):
  self.assertEqual([self.feed.index(self.feed.anchor+offset) for offset in (0,5,10,15,20)],[1,2,3,4,1])
 def test_pause_resume_and_settings_persist(self):
  at=self.feed.anchor+11
  with patch('simulator_camera.time.monotonic',return_value=at):self.feed.configure({'interval_s':2,'paused':True})
  self.assertEqual(self.feed.index(at+100),3);restored=CameraFeed(self.app,ROOT/'frontend/public');self.assertTrue(restored.paused);self.assertEqual(restored.interval,2);self.assertEqual(restored.index(),3)
  with patch('simulator_camera.time.monotonic',return_value=at+100):self.feed.configure({'interval_s':2,'paused':False})
  self.assertEqual(self.feed.index(at+102),4);self.assertEqual(self.feed.index(at+104),1)
 def test_snapshot_and_fixed_frame_http_headers(self):
  self.feed.configure({'interval_s':5,'paused':True});status,raw,headers=self.call('/mock/camera/snapshot.jpg?_t=123');self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/jpeg');self.assertEqual(headers['X-Simulation'],'true');self.assertIn('no-store',headers['Cache-Control']);self.assertEqual(raw,self.feed.frame()[1])
  for i in range(1,5):self.assertEqual(self.call('/mock/camera/frames/'+str(i)+'.jpg')[1],self.feed.frame(i)[1])
  self.assertEqual(self.call('/mock/camera/frames/5.jpg')[0],404);self.assertEqual(self.call('/mock/camera/snapshot.jpg',{})[0],405)
 def test_mjpeg_stream_changes_photos_without_blocking_management(self):
  self.feed.configure({'interval_s':1,'paused':False});c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=6);c.request('GET','/mock/camera/stream.mjpeg');r=c.getresponse()
  try:
   self.assertEqual(r.status,200);self.assertIn('multipart/x-mixed-replace',r.getheader('Content-Type'));frames=[]
   for _ in range(2):
    self.assertEqual(r.readline(),b'--tianshu-frame\r\n');headers={}
    while True:
     line=r.readline()
     if line==b'\r\n':break
     key,value=line.decode().strip().split(':',1);headers[key]=value.strip()
    raw=r.read(int(headers['Content-Length']));self.assertEqual(r.read(2),b'\r\n');jpeg_size(raw);frames.append((int(headers['X-Frame-Index']),hashlib.sha256(raw).hexdigest()))
    self.assertEqual(self.call('/api/health')[0],200)
   self.assertEqual(frames[1][0],frames[0][0]%4+1);self.assertNotEqual(frames[0][1],frames[1][1])
  finally:self.app.stop.set();r.close();c.close()
 def test_stream_connection_limit(self):
  for _ in range(4):self.assertTrue(self.feed.limit.acquire(False))
  try:self.assertEqual(self.call('/mock/camera/stream.mjpeg')[0],429)
  finally:
   for _ in range(4):self.feed.limit.release()
 def test_camera_settings_and_upload_require_csrf(self):
  self.assertEqual(self.call('/sim/camera-settings',{'interval_s':1,'paused':False})[0],401);headers=self.auth();self.assertEqual(self.call('/sim/camera-settings',{'interval_s':1,'paused':False},{'Cookie':headers['Cookie']})[0],403);self.assertEqual(self.call('/sim/camera-settings',{'interval_s':0,'paused':False},headers)[0],400);self.assertEqual(self.call('/sim/camera-settings',{'interval_s':1.5,'paused':False},headers)[0],400);self.assertEqual(self.call('/sim/camera-settings',{'interval_s':3,'paused':False},headers)[0],200)
 def test_photo_replace_is_local_and_does_not_change_bundled_default(self):
  headers=self.auth();original=(ROOT/'frontend/public/monitor/frame4.jpg').read_bytes();replacement=self.feed.frame(1)[1];status,_,_=self.call('/sim/camera-photo',{'index':4,'data':base64.b64encode(replacement).decode()},headers);self.assertEqual(status,200);self.assertEqual(self.feed.frame(4)[1],replacement);self.assertTrue(self.feed.status()['photos'][3]['custom']);self.assertEqual((ROOT/'frontend/public/monitor/frame4.jpg').read_bytes(),original)
  self.assertEqual(self.call('/sim/camera-photo',{'index':5,'data':'a'},headers)[0],400);self.assertEqual(self.call('/sim/camera-photo',{'index':1,'data':base64.b64encode(b'not a JPEG').decode()},headers)[0],400)
 def test_main_system_accepts_realtime_jpeg_camera(self):
  energy=Energy(Path(self.tmp.name)/'main-system',seed=False);admin={'id':'synthetic','username':'synthetic','role':'admin'}
  try:
   space=energy.save_space(admin,{'name':'模拟监控测试'});camera=energy.save_camera(admin,{'space_id':space['id'],'name':'【模拟】四照片轮播','area':'测试','kind':'snapshot','url':self.server.origin+'/mock/camera/snapshot.jpg'});self.assertEqual(camera['kind'],'snapshot');self.assertIn('/mock/camera/snapshot.jpg',camera['url'])
  finally:energy.close()

if __name__=='__main__':unittest.main(verbosity=2)
