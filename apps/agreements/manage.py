"""Administrative CLI; production bootstrap keys are never placed in source."""
import argparse,secrets
from pathlib import Path
import database as db
from sqlalchemy import select
parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','setup-key','check','install-signatures']);args=parser.parse_args()
db.init_db()
if args.command=='install-signatures':
    source=db.APP/'private-assets'/'signatures';target=db.DATA/'signatures';target.mkdir(parents=True,exist_ok=True)
    copied=0
    for name in ('Naeem Abbas','Danish Ather'):
        for suffix in ('.png','.json'):
            src=source/(name+suffix);dst=target/(name+suffix)
            if src.is_file() and not dst.exists():
                with dst.open('xb') as output:output.write(src.read_bytes())
                copied+=1
    print(str(copied)+' authorized signature assets installed; existing assets were not overwritten.')
elif args.command=='setup-key':
    with db.engine.connect() as c:
        if c.execute(select(db.users.c.id).limit(1)).first():raise SystemExit('Workspace already initialized.')
    path=db.DATA/'setup-key.txt';path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_text(secrets.token_urlsafe(24),encoding='utf-8')
    print('Setup key saved privately to '+str(path))
elif args.command=='check':
    with db.engine.connect() as c:c.execute(select(1))
    print('Database connection is healthy. Mode: '+('local SQLite' if db.URL.startswith('sqlite') else 'PostgreSQL'))
else:print('Schema initialized.')
