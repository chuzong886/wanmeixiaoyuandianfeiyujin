from pathlib import Path
import subprocess,shutil,zipfile
root=Path(__file__).resolve().parents[1];classes=root/'build/api-classes';classes.mkdir(parents=True,exist_ok=True);target=root/'api-runtime';target.mkdir(exist_ok=True)
subprocess.run(['javac','-encoding','UTF-8','--release','8','-cp',str(root/'adapters/vendor/json.jar'),'-d',str(classes)]+[str(x) for x in (root/'adapters/src').rglob('*.java')],check=True)
with zipfile.ZipFile(target/'adapters.jar','w',zipfile.ZIP_DEFLATED) as z:
 for p in classes.rglob('*.class'):z.write(p,p.relative_to(classes).as_posix())
shutil.copy2(root/'adapters/vendor/json.jar',target/'json.jar')
runtime=root/'.toolchain/java'
if not runtime.exists():subprocess.run(['jlink','--add-modules','java.base,java.logging,jdk.crypto.ec','--strip-debug','--no-header-files','--no-man-pages','--output',str(runtime)],check=True)
shutil.copytree(runtime,target/'runtime',dirs_exist_ok=True)
print('Existing campus protocol, capture and conservative forecast packaged as a device adapter')
