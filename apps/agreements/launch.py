"""Local launcher: one API process and one supervised document worker."""
import argparse,os,json,secrets,subprocess,sys,threading,time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    os.environ['TFSC_ORIGIN']=f'http://127.0.0.1:{args.port}'
    import database as db
    from sqlalchemy import select
    db.init_db()
    (db.DATA/'stop-request.json').unlink(missing_ok=True)
    with db.engine.connect() as c:first=c.execute(select(db.users.c.id).limit(1)).first() is None
    if first and not (db.DATA/'setup-key.txt').exists():(db.DATA/'setup-key.txt').write_text(secrets.token_urlsafe(24),encoding='utf-8')
    stop=threading.Event();child=None
    import uvicorn
    server=uvicorn.Server(uvicorn.Config('portal:app',host='127.0.0.1',port=args.port,access_log=False))
    def supervise():
        nonlocal child
        while not stop.is_set():
            with (db.DATA/'worker.log').open('ab') as log:
                child=subprocess.Popen([sys.executable,str(db.APP/'worker.py')],cwd=db.APP,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=log,stderr=log)
                (db.DATA/'process.json').write_text(json.dumps({'api_pid':os.getpid(),'worker_pid':child.pid,'port':args.port}),encoding='utf-8')
                while child.poll() is None and not stop.wait(1):
                    if (db.DATA/'stop-request.json').exists():
                        # The worker finishes its current export before exiting.
                        stop.set()
                        while child.poll() is None:time.sleep(.5)
                        server.should_exit=True
                if (db.DATA/'stop-request.json').exists():stop.set();server.should_exit=True
                if stop.is_set():break
                stop.wait(3)
    thread=threading.Thread(target=supervise,daemon=True);thread.start()
    try:server.run()
    finally:
        (db.DATA/'stop-request.json').write_text(json.dumps({'api_pid':os.getpid()}))
        stop.set();thread.join(5)
if __name__=='__main__':main()
