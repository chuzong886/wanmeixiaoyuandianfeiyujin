from pathlib import Path
import hashlib,json,shutil,zipfile
root=Path(__file__).resolve().parents[1];dist=root/'dist';windows=dist/'windows-1.0.0';dist.mkdir(exist_ok=True)
for name in ['README.md','THIRD_PARTY.md']+(['design-qa.md'] if (root/'design-qa.md').is_file() else []):
 shutil.copy2(root/name,windows/name)
shutil.copytree(root/'docs',windows/'docs',dirs_exist_ok=True)
# Ensure both distributions contain the exact same backend and web resources.
for folder in ['backend','web','api-runtime']:
 for p in (root/folder).rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':assert (windows/folder/p.relative_to(root/folder)).read_bytes()==p.read_bytes(),f'Stale release file: {p.name}'
for name in ['Tianshu.Energy.exe','Tianshu.Energy.dll','Tianshu.Energy.runtimeconfig.json','runtime/python.exe','runtime/python312._pth','backend/server.py','backend/energy.py','backend/thirdparty.py','api-runtime/adapters.jar','api-runtime/json.jar','api-runtime/runtime/bin/java.exe','web/index.html']:
 assert (windows/name).is_file(),f'Missing runtime file {name}'
assert '../backend' in (windows/'runtime/python312._pth').read_text()
winzip=dist/'Tianshu-Energy-1.0.0-Windows-x64.zip'
winwork=winzip.with_suffix('.zip.partial')
with zipfile.ZipFile(winwork,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
 for p in sorted(windows.rglob('*')):
  if not p.is_file() or p.suffix in ['.pdb','.pyc'] or '__pycache__' in p.parts:continue
  assert p.suffix.lower() not in ['.sqlite','.sqlite3','.log','.dpapi','.enc','.jks'],f'Runtime data included: {p.name}'
  assert not any(t in p.name for t in ['.sqlite-wal','.sqlite-shm'])
  z.write(p,'Tianshu-Energy/'+p.relative_to(windows).as_posix())
with zipfile.ZipFile(winwork) as check:assert check.testzip() is None
winwork.replace(winzip)
print('Windows distribution packaged',flush=True)
source=dist/'Tianshu-Energy-1.0.0-source.zip';files=set()
for folder in ['backend','frontend/src','frontend/public','launcher','tools','tests','docs','licenses','web','adapters']:
 for p in (root/folder).rglob('*'):
  relative=p.relative_to(root)
  if p.is_file() and not any(x in relative.parts for x in ['bin','obj','node_modules','__pycache__','build','data','dist']):files.add(p)
for name in ['README.md','THIRD_PARTY.md','AGENTS.md','.gitignore','requirements.txt','frontend/package.json','frontend/package-lock.json','frontend/tsconfig.json','frontend/vite.config.js','frontend/build-locales.mjs','frontend/index.html','frontend/simulator.html']+(['design-qa.md'] if (root/'design-qa.md').is_file() else []):files.add(root/name)
with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in sorted(files):
  if p.name in ['prepare.py'] or p.suffix in ['.pyc','.pdb']:continue
  assert p.suffix.lower() not in ['.sqlite','.sqlite3','.log','.dpapi','.enc','.jks']
  z.write(p,'tianshu-energy/'+p.relative_to(root).as_posix())
for archive in [winzip,source]:
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None
 print(archive.name+': '+str(round(archive.stat().st_size/1024/1024,2))+' MiB',flush=True)
sums=[]
for archive in [winzip,source]:sums.append(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name)
(dist/'SHA256SUMS.txt').write_text('\n'.join(sums)+'\n',encoding='utf-8')
print('Release archives, source synchronization and SHA256 verified',flush=True)
