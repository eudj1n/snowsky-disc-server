"""Combined-008 acceptance inside the disposable guest: self-recovery and the card switch.

`crash`: the MIPS service runs under its supervisor (qemu-user forks it); killing the service
process leaves the supervisor to start a new one, which answers again, and the restart log names
the signal. `switch`: a .disc/disabled folder on the card stops the service within seconds and the
supervisor does not start it again; the switch is removed at the end and the host restarts the
service. Disposable guest only.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, '/platform/tests/integration')
from gateway_mutation import call  # noqa: E402

ROOT = Path('/work/rootfs')
SWITCH = Path('/tmp/sdcard/.disc/disabled')
LOG = ROOT/'run/disc-web-restarts.log'
OUT = Path('/work/disc-recovery.json')


def services():
    """(pid, parent pid) of the guest's disc-service processes."""
    found = []
    for proc in Path('/proc').iterdir():
        try:
            if not proc.name.isdigit() or (proc/'root').resolve() != ROOT.resolve():
                continue
            if b'/usr/data/disc-service' not in (proc/'cmdline').read_bytes().split(b'\0'):
                continue
            ppid = int(next(l for l in (proc/'status').read_text().splitlines() if l.startswith('PPid:')).split()[1])
            found.append((int(proc.name), ppid))
        except (OSError, StopIteration, ValueError):
            continue
    pids = {pid for pid, _ in found}
    supervisor = [pid for pid, ppid in found if ppid not in pids]
    child = [pid for pid, ppid in found if ppid in pids]
    return (supervisor[0] if supervisor else None), (child[0] if child else None)


def healthy(deadline):
    while time.monotonic() < deadline:
        try:
            if call('GET', '/api/health')[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(.2)
    return False


def main():
    phase = sys.argv[1]
    summary = json.loads(OUT.read_text()) if phase == 'switch' and OUT.exists() else {}
    supervisor, child = services()
    assert supervisor and child, (supervisor, child)
    if phase == 'crash':
        before = LOG.read_text() if LOG.exists() else ''
        os.kill(child, signal.SIGKILL)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and services()[1] in (None, child):
            time.sleep(.2)
        assert services()[1] not in (None, child) and healthy(time.monotonic() + 30), services()
        added = LOG.read_text()[len(before):]
        assert added.split(' ', 1)[1].strip() == 'restarted after signal 9', added
        summary['crash'] = {'supervisor': 'kept', 'restarted': True, 'log': added.split(' ', 1)[1].strip()}
    else:
        SWITCH.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        while time.monotonic() - started < 20 and any(services()):
            time.sleep(.2)
        stopped = round(time.monotonic() - started, 1)
        assert not any(services()), services()
        assert LOG.read_text().rstrip().endswith('disabled by the card switch'), LOG.read_text()[-200:]
        SWITCH.rmdir()
        summary['switch'] = {'stopped_s': stopped, 'log': 'disabled by the card switch'}
    summary['status'] = 'passed' if phase == 'switch' else 'crash passed'
    OUT.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
