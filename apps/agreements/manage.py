"""Administrative CLI; production bootstrap keys are never placed in source."""
import argparse,secrets
from pathlib import Path
import database as db
from sqlalchemy import select
parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','setup-key','check','install-signatures','link-crm-admin']);parser.add_argument('value',nargs='?');args=parser.parse_args()
db.init_db()
if args.command=='install-signatures':
    source=db.APP/'private-assets'/'signatures';target=db.DATA/'signatures';target.mkdir(parents=True,exist_ok=True);target.chmod(0o700)
    copied=0
    for name in ('Naeem Abbas','Danish Ather'):
        for suffix in ('.png','.json'):
            src=source/(name+suffix);dst=target/(name+suffix)
            if src.is_file() and not dst.exists():
                with dst.open('xb') as output:output.write(src.read_bytes())
                dst.chmod(0o600)  # private to the app user, whatever the caller's umask
                copied+=1
    print(str(copied)+' authorized signature assets installed; existing assets were not overwritten.')
elif args.command=='setup-key':
    with db.engine.connect() as c:
        if c.execute(select(db.users.c.id).limit(1)).first():raise SystemExit('Workspace already initialized.')
    path=db.DATA/'setup-key.txt';path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_text(secrets.token_urlsafe(24),encoding='utf-8')
    print('Setup key saved privately to '+str(path))
elif args.command=='link-crm-admin':
    # The CRM Super Admin acts in Agreements as the first Agreements Super Admin (deploy/vps/tfsc.sh link-admins).
    import crm_bridge
    from fastapi import HTTPException
    with db.engine.begin() as c:
        admin=c.execute(select(db.users).where(db.users.c.role=='super_admin',db.users.c.active==True).order_by(db.users.c.created).limit(1)).mappings().first()
        if not admin:raise SystemExit('Set up the Agreements Super Admin first: open the Agreements address and use the setup key.')
        try:crm_bridge.link_actor(c,dict(admin),(args.value or '').strip().lower(),admin['id'])
        except HTTPException as e:raise SystemExit(e.detail) from None
    print('CRM actor '+args.value.strip().lower()+' linked to the Agreements Super Admin '+admin['email']+'.')
elif args.command=='check':
    with db.engine.connect() as c:c.execute(select(1))
    print('Database connection is healthy. Mode: '+('local SQLite' if db.URL.startswith('sqlite') else 'PostgreSQL'))
else:print('Schema initialized.')
