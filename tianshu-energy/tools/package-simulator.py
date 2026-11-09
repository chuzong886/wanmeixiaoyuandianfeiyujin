"""Stage an independent simulator, never copying databases or user credentials."""
from pathlib import Path
import shutil,zipfile,hashlib
root=Path(__file__).resolve().parents[1];target=root/'dist/simulator-1.0.1';compiled=root/'build/simulator-launcher'
assert (compiled/'Tianshu.Energy.exe').is_file(),'Publish the existing launcher first'
assert (root/'web/simulator.html').is_file(),'Build the multi-entry frontend first'
target.mkdir(parents=True,exist_ok=True)
def copy_changed(src,dst):
 if Path(dst).is_file() and Path(src).read_bytes()==Path(dst).read_bytes():return dst
 return shutil.copy2(src,dst)
shutil.copytree(compiled,target,dirs_exist_ok=True,copy_function=copy_changed,ignore=shutil.ignore_patterns('Tianshu.Energy.exe','*.pdb'))
copy_changed(compiled/'Tianshu.Energy.exe',target/'Tianshu.Simulator.exe')
for src,dst in [('.toolchain/python','runtime'),('backend','backend'),('web','web'),('licenses','licenses')]:shutil.copytree(root/src,target/dst,dirs_exist_ok=True,copy_function=copy_changed,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
copy_changed(root/'THIRD_PARTY.md',target/'THIRD_PARTY.md')
copy_changed(root/'docs/模拟平台使用说明.md',target/'使用说明.md')
if (root/'docs/模拟平台验收记录.md').exists():copy_changed(root/'docs/模拟平台验收记录.md',target/'模拟平台验收记录.md')
if (root/'docs/simulator').exists():shutil.copytree(root/'docs/simulator',target/'simulator',dirs_exist_ok=True,copy_function=copy_changed)
archive=root/'dist/Tianshu-Simulator-1.0.1-Windows-x64.zip';partial=archive.with_suffix('.zip.partial')
with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
 for p in sorted(target.rglob('*')):
  if not p.is_file() or p.suffix=='.pyc' or '__pycache__' in p.parts:continue
  assert p.suffix.lower() not in ('.sqlite','.sqlite3','.db','.log','.enc','.dpapi','.pdb','.pyc'),p.name
  assert not any(x in p.parts for x in ('data','build','simulator-data','__pycache__'))
  assert '.sqlite-' not in p.name
  z.write(p,'Tianshu-Simulator/'+p.relative_to(target).as_posix())
with zipfile.ZipFile(partial) as z:assert z.testzip() is None
partial.replace(archive);digest=hashlib.sha256(archive.read_bytes()).hexdigest();(archive.parent/'SIMULATOR-SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
print('PASS: simulator staged, archive integrity and no database/credential payload verified')
print(str(archive)+' ('+str(round(archive.stat().st_size/1024/1024,1))+' MiB)')
