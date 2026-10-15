"""Build a scanned index snapshot, never collect runtime directories."""
from pathlib import Path
import hashlib
import subprocess
import sys
import zipfile

root = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, str(root / "tools/scan-secrets.py")], cwd=root, check=True)
rows = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0")
target = root / "dist/campus-power-alert-source.zip"
target.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
    for row in rows:
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        _, oid, _ = metadata.split()
        data = subprocess.check_output(["git", "cat-file", "blob", oid.decode()], cwd=root)
        archive.writestr("campus-power-alert/" + name.decode("utf-8"), data)
digest = hashlib.sha256(target.read_bytes()).hexdigest()
target.with_suffix(".sha256").write_text(digest + "  " + target.name + "\n", encoding="utf-8")
print("Built scanned source archive in dist/ (runtime data excluded)")
