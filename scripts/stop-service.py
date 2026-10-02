"""Stop only this project's service in this disposable guest rootfs."""
import os
from pathlib import Path
import signal
import time
assert os.environ.get('CI_DISPOSABLE') == '1', 'Disposable emulator required'
root = Path('/work/rootfs').resolve()
targets=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdecimal(): continue
    try:
        if (proc/'root').resolve() != root: continue
        if b'/usr/data/disc-service' not in (proc/'cmdline').read_bytes().split(b'\0'): continue
        os.kill(int(proc.name), signal.SIGTERM); targets.append(proc)
    except (OSError, ProcessLookupError): pass
deadline=time.monotonic()+12
while any(p.exists() for p in targets):
    if time.monotonic()>deadline: raise SystemExit('Service did not stop; inspect before restarting')
    time.sleep(.1)
