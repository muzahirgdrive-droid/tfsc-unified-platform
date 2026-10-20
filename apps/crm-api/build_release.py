"""Package the current apps. Never include client databases, credentials or exports."""
from pathlib import Path
import hashlib, zipfile

APP=Path(__file__).resolve().parent
OUT=APP.parent
ZIP=OUT/'TFSC-Sales-and-Compliance-VPS-v1.0.1.zip'
BLOCKED={'.data','.venv','node_modules','__pycache__','.pytest_cache','.git','runtime','examples','private-assets'}

def include(path, root):
    parts=path.relative_to(root).parts
    if any(p in BLOCKED for p in parts):return False
    if path.name=='.env' or path.name.endswith(('.db','.db-wal','.db-shm','.sqlite','.sqlite3','.pyc','.log','.zip','.key','.tsbuildinfo')):return False
    return True

def main():
    entries={}
    for name in ('TFSCSalesCRM','ScribeAlignContractStudio','TFSC-Sales-CRM-Architecture'):
        root=OUT/name
        for path in sorted(root.rglob('*')):
            if path.is_file() and include(path,root):entries[name+'/'+path.relative_to(root).as_posix()]=path.read_bytes()
    # The user explicitly requested a complete private handoff with saved signatures.
    # Whitelist only the two authorized image files and their crop metadata.
    for name in ('Naeem Abbas','Danish Ather'):
        for ext in ('.png','.json'):
            path=OUT/'ScribeAlignContractStudio'/'.data'/'signatures'/(name+ext)
            if not path.exists():raise RuntimeError('Required authorized signature asset is missing: '+name+ext)
            entries['ScribeAlignContractStudio/private-assets/signatures/'+name+ext]=path.read_bytes()
    for name in ('TFSC-CRM-dashboard.jpg','TFSC-CRM-mobile.jpg','TFSC-user-form-fix-v1.0.1.jpg'):
        entries['Previews/'+name]=(OUT/name).read_bytes()
    entries['START-HERE.txt']=('TFSC SALES + COMPLIANCE: CURRENT IMPLEMENTED PILOT\n'
        'Read TFSCSalesCRM/README.txt and DEPLOYMENT.txt first.\n'
        'This is the complete current application source, compiled CRM UI, TFSC document\n'
        'engine, templates and authorized signature assets, not an integration-only kit.\n'
        'No client records, live databases, setup keys or passwords are included.\n'
        'The archive contains signature images: share privately with your authorized developer.\n'
        'Python dependencies install from the pinned requirements; Node 22 builds the UI.\n'
        'The old TFSC README describes a bundled Windows runtime. THIS VPS/source archive\n'
        'does not bundle that runtime: create a Python environment and install dependencies.\n'
        'The architecture folder describes the larger roadmap; README explicitly lists\n'
        'unfinished features and production-scale verification still required.\n').encode()
    manifest=''.join(hashlib.sha256(body).hexdigest()+'  '+name+'\n' for name,body in sorted(entries.items()))
    entries['MANIFEST.sha256']=manifest.encode()
    with zipfile.ZipFile(ZIP,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,body in sorted(entries.items()):z.writestr(name,body)
    with zipfile.ZipFile(ZIP) as z:
        assert z.testzip() is None
        for name,body in entries.items():assert hashlib.sha256(z.read(name)).digest()==hashlib.sha256(body).digest()
        assert not any('/.data/' in n or '/node_modules/' in n or n.endswith('/.env') for n in z.namelist())
        assert 'TFSCSalesCRM/web/dist/index.html' in z.namelist()
        assert 'ScribeAlignContractStudio/contract_content.py' in z.namelist()
        for name in ('letterhead.docx','letterhead-ehs.docx','letterhead-24-7.docx'):
            assert 'ScribeAlignContractStudio/assets/'+name in z.namelist()
    digest=hashlib.sha256(ZIP.read_bytes()).hexdigest()
    ZIP.with_suffix('.sha256.txt').write_text(digest+'  '+ZIP.name+'\n')
    print(f'{ZIP}\n{len(entries)} files; {ZIP.stat().st_size:,} bytes\nSHA256 {digest}')

if __name__=='__main__':main()
