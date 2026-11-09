from pathlib import Path
import urllib.request,zipfile,hashlib
root=Path(__file__).resolve().parents[1];folder=root/'.toolchain';folder.mkdir(exist_ok=True);archive=folder/'python-3.12.10-embed-amd64.zip'
url='https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
if not archive.exists():urllib.request.urlretrieve(url,archive)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
if digest!='4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3':raise RuntimeError('Embedded runtime checksum mismatch')
print('Official Python embedded runtime checksum verified',flush=True)
with zipfile.ZipFile(archive) as z:
 assert z.testzip() is None;z.extractall(folder/'python')
(folder/'python/python312._pth').write_text('python312.zip\n.\n../backend\n',encoding='utf-8')
print('Official embedded Python runtime ready',flush=True)
