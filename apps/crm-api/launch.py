"""Local launcher with an owned process record and graceful stop file."""
import json, os, threading, time
from pathlib import Path
import uvicorn
import crm_db as db

def main():
    os.environ.setdefault('CRM_MODE','local')
    os.environ.setdefault('CRM_ORIGIN','http://127.0.0.1:8766')
    state=db.DATA/'process.json';stop=db.DATA/'stop-request'
    stop.unlink(missing_ok=True)
    state.write_text(json.dumps({'pid':os.getpid(),'app_path':str(db.APP),'port':8766}))
    server=uvicorn.Server(uvicorn.Config('crm_app:app',host='127.0.0.1',port=8766,access_log=False))
    def watch():
        while not server.should_exit:
            if stop.exists():server.should_exit=True;break
            time.sleep(.5)
    threading.Thread(target=watch,daemon=True).start()
    try:server.run()
    finally:state.unlink(missing_ok=True);stop.unlink(missing_ok=True)

if __name__=='__main__':main()
