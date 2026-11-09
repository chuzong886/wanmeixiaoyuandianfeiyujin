"""Four-photo local simulation feed. No camera devices, external downloads or video codecs."""
import base64,json,re,threading,time
from pathlib import Path
from energy import Error,number,jdump

MAX_PHOTO=3*1024*1024
def jpeg_size(raw):
 if not 4<=len(raw)<=MAX_PHOTO or raw[:2]!=b'\xff\xd8' or raw[-2:]!=b'\xff\xd9':raise Error('请选择3MB以内的完整 JPEG 照片')
 pos=2
 while pos+4<=len(raw):
  if raw[pos]!=255:raise Error('JPEG 格式无效')
  while pos<len(raw) and raw[pos]==255:pos+=1
  if pos>=len(raw):break
  marker=raw[pos];pos+=1
  if marker==0xDA:break
  if marker in (0x01,0xD8) or 0xD0<=marker<=0xD7:continue
  length=int.from_bytes(raw[pos:pos+2],'big')
  if length<2 or pos+length>len(raw):raise Error('JPEG 数据不完整')
  if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF):
   if length<8:raise Error('JPEG 尺寸无效')
   height=int.from_bytes(raw[pos+3:pos+5],'big');width=int.from_bytes(raw[pos+5:pos+7],'big')
   if not 16<=width<=4096 or not 16<=height<=4096 or width*height>8000000:raise Error('照片边长应为16至4096像素，总像素不超过800万')
   return width,height
  pos+=length
 raise Error('无法识别 JPEG 照片尺寸')

class CameraFeed:
 def __init__(self,app,web):
  self.app=app;self.web=Path(web).resolve();self.directory=app.directory/'monitor';self.lock=threading.RLock();self.limit=threading.BoundedSemaphore(4);self.anchor=time.monotonic();self.offset=0;self.cache={}
  with app.lock:r=app.db.execute("SELECT value FROM settings WHERE key='camera'").fetchone()
  try:c=json.loads(r[0]) if r else {};self.interval=int(number(c.get('interval_s',5),1,60));self.paused=c.get('paused') is True;self.offset=int(number(c.get('frame_index',1),1,4))-1
  except (Error,ValueError,TypeError):self.interval=5;self.paused=False;self.offset=0
 def index(self,now=None):
  now=time.monotonic() if now is None else now
  return (self.offset+(0 if self.paused else max(0,int((now-self.anchor)/self.interval))))%4+1
 def path(self,index):
  if type(index) is not int or index not in (1,2,3,4):raise Error('照片编号应为1至4',404)
  custom=self.directory/('frame'+str(index)+'.jpg')
  return custom if custom.is_file() else self.web/'monitor'/custom.name
 def frame(self,index=None):
  with self.lock:
   index=self.index() if index is None else index;path=self.path(index)
   if not path.is_file():raise Error('模拟照片未找到，请检查运行包或上传照片',503)
   stamp=path.stat().st_mtime_ns;key=(str(path),stamp)
   if key not in self.cache:
    raw=path.read_bytes();jpeg_size(raw);self.cache={k:v for k,v in self.cache.items() if k[0]!=str(path)};self.cache[key]=raw
   return index,self.cache[key]
 def status(self):
  with self.lock:
   photos=[]
   for i in range(1,5):
    p=self.path(i);photos.append({'index':i,'custom':p.parent==self.directory,'available':p.is_file(),'revision':str(p.stat().st_mtime_ns) if p.is_file() else 'missing'})
   return {'simulation':True,'total':4,'frame_index':self.index(),'interval_s':self.interval,'paused':self.paused,'photos':photos}
 def configure(self,b):
  seconds=number(b.get('interval_s',5),1,60)
  if int(seconds)!=seconds or type(b.get('paused',False)) is not bool:raise Error('轮换间隔应为1至60的整数秒，暂停状态应为开关值')
  with self.lock:
   self.offset=self.index()-1;self.anchor=time.monotonic();self.interval=int(seconds);self.paused=b.get('paused',False);saved={'interval_s':self.interval,'paused':self.paused,'frame_index':self.offset+1}
   with self.app.lock:self.app.db.execute("INSERT OR REPLACE INTO settings VALUES('camera',?)",(jdump(saved),));self.app.event('camera','更新四照片模拟监控轮换设置');self.app.db.commit()
   return self.status()
 def upload(self,b):
  index=b.get('index')
  if type(index) is not int or index not in (1,2,3,4):raise Error('照片编号应为1至4')
  try:raw=base64.b64decode(b.get('data',''),validate=True)
  except (ValueError,TypeError):raise Error('照片编码无效')
  jpeg_size(raw)
  with self.lock:
   self.directory.mkdir(parents=True,exist_ok=True);target=self.directory/('frame'+str(index)+'.jpg');temp=target.with_suffix('.jpg.partial');temp.write_bytes(raw);temp.replace(target)
   with self.app.lock:self.app.event('camera','替换模拟照片 '+str(index));self.app.db.commit()
   return self.status()
 def stream(self,handler):
  if not self.limit.acquire(False):raise Error('模拟监控最多同时4个观看连接',429)
  started=False
  try:
   index,raw=self.frame();handler.send_response(200);handler.send_header('Content-Type','multipart/x-mixed-replace; boundary=tianshu-frame');handler.send_header('Cache-Control','no-store, no-cache, must-revalidate');handler.send_header('X-Simulation','true');handler.send_header('X-Content-Type-Options','nosniff');handler.send_header('Connection','close');handler.end_headers();started=True;previous=None;last_sent=0
   while not self.app.stop.is_set():
    now=time.monotonic();index,raw=self.frame();signature=(index,id(raw))
    if signature!=previous or now-last_sent>=1:
     part=b'--tianshu-frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(raw)).encode()+b'\r\nX-Simulation: true\r\nX-Frame-Index: '+str(index).encode()+b'\r\n\r\n';handler.wfile.write(part+raw+b'\r\n');handler.wfile.flush();previous=signature;last_sent=now
    self.app.stop.wait(.1)
  except Error:
   if not started:raise
  except (OSError,TimeoutError):pass
  finally:handler.close_connection=True;self.limit.release()

def photo_index(path):
 match=re.fullmatch(r'/mock/camera/frames/([1-4])\.jpg',path)
 return int(match[1]) if match else None
