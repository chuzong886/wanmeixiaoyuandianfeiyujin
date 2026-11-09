from pathlib import Path
import shutil,subprocess,json
root=Path(__file__).resolve().parents[1];target=root/'dist/windows-1.0.0';target.mkdir(parents=True,exist_ok=True)
def copy_if_changed(src,dst):
 # A running older preview may hold runtime DLLs open. Never rewrite identical files.
 if Path(dst).is_file() and Path(src).read_bytes()==Path(dst).read_bytes():return dst
 return shutil.copy2(src,dst)
shutil.copytree(root/'.toolchain/python',target/'runtime',dirs_exist_ok=True,copy_function=copy_if_changed)
shutil.copytree(root/'backend',target/'backend',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
web_target=(target/'web').resolve();assert web_target.is_relative_to(root.resolve()/'dist')
if web_target.exists():
 for old in web_target.rglob('*'):
  if old.is_file() and not (root/'web'/old.relative_to(web_target)).exists():old.unlink()
shutil.copytree(root/'web',web_target,dirs_exist_ok=True)
shutil.copytree(root/'api-runtime',target/'api-runtime',dirs_exist_ok=True,copy_function=copy_if_changed)
shutil.copytree(root/'licenses',target/'licenses',dirs_exist_ok=True)
subprocess.run([str(target/'runtime/python.exe'),'-c','import sqlite3,ssl,hashlib,socket; import energy; print("Bundled backend imports OK; SQLite",sqlite3.sqlite_version)'],check=True)
print('Independent Windows runtime staged')
