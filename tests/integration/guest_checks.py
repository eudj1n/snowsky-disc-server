"""Admission checks for tests running inside our disposable emulator container."""
import os
from pathlib import Path


def stock_processes():
    assert os.environ.get('CI_DISPOSABLE') == '1', 'Disposable guest required'
    root = Path('/work/rootfs')
    found = {name: [] for name in ('mq_ui', 'mq_player')}
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if (process/'root').resolve() != root:
                continue
            argv = (process/'cmdline').read_bytes().split(b'\0')
            for name in found:
                if ('/usr/bin/'+name).encode() in argv:
                    found[name].append(int(process.name))
        except OSError:
            continue
    assert all(len(pids) == 1 for pids in found.values()), (
        'Expected one live stock UI and player; recreate the disposable stack', found)
    return {name: pids[0] for name, pids in found.items()}


def native_resources():
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if (process/'root').resolve() != Path('/work/rootfs'):
                continue
            if b'/usr/data/disc-service' not in (process/'cmdline').read_bytes().split(b'\0'):
                continue
            status = dict(line.split(':', 1) for line in (process/'status').read_text().splitlines())
            found.append(dict(pid=int(process.name), qemuVmRSS=status['VmRSS'].strip(),
                              qemuVmSize=status['VmSize'].strip(), threads=status['Threads'].strip(),
                              fds=len(list((process/'fd').iterdir())), ppid=int(status['PPid'].strip())))
        except OSError:
            continue
    # Since combined-008 a supervisor process runs the service as its child: the
    # service is the process whose parent is the supervisor.
    pids = {entry['pid'] for entry in found}
    services = [entry for entry in found if entry['ppid'] in pids] or found
    assert len(services) == 1 and len(found) - len(services) <= 1, found
    service = dict(services[0])
    del service['ppid']
    return service
