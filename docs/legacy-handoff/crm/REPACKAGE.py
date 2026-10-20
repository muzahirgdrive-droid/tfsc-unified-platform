"""Repackage this source handoff on any PC. Excludes live state and credentials."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, zipfile
root=Path(__file__).resolve().parents[1]
blocked={'.data','.venv','node_modules','__pycache__','.pytest_cache','.git','runtime','examples'}
target=root.parent/(root.name+'-handoff-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.zip')
entries={}
for p in root.rglob('*'):
    if not p.is_file():continue
    rel=p.relative_to(root)
    if any(part in blocked for part in rel.parts) or p.name in ('.env','HANDOFF-MANIFEST.sha256') or p.name.endswith(('.db','.db-wal','.db-shm','.sqlite','.sqlite3','.key','.pem','.pyc','.log','.zip','.tsbuildinfo')):continue
    entries[rel.as_posix()]=p.read_bytes()
manifest=''.join(hashlib.sha256(body).hexdigest()+'  '+name+'\n' for name,body in sorted(entries.items()))
entries['HANDOFF-MANIFEST.sha256']=manifest.encode()
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
    for name,body in sorted(entries.items()):z.writestr(root.name+'/'+name,body)
with zipfile.ZipFile(target) as z:assert z.testzip() is None
target.with_suffix('.sha256.txt').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
print(target)
print('Contains authorized signature assets if present. Share privately. No live database or .env included.')
