"""Shared facts for the checks inside the disposable guest's container (scripts/emulator.py).

The gateway runs as the boot layer's package and listens on the ports the
player gives it, published on host loopback under the same numbers, so the
same authorities hold inside the container and on the host.
"""
import os
from pathlib import Path

PORT, MANAGER_PORT = 7870, 7871
AUTHORITY, MANAGER_AUTHORITY = f'127.0.0.1:{PORT}', f'127.0.0.1:{MANAGER_PORT}'
ROOTFS = Path('/work/rootfs')
CARD = Path('/tmp/sdcard')
SERIAL = '00000000000000'  # the guest's synthetic serial number (snowsky-disc-boot scripts/guest.py)


def guest_processes(name):
    """Container PIDs of a guest program, by /proc/<pid>/comm as on the player (the emulator's
    advice: a static program's cmdline and exe still name qemu)."""
    found = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if (process/'root').resolve() != ROOTFS or (process/'comm').read_text().strip() != name:
                continue
            found.append(int(process.name))
        except OSError:
            continue
    return found


def stock_processes():
    assert os.environ.get('CI_DISPOSABLE') == '1', 'Disposable guest required'
    found = {name: guest_processes(name) for name in ('mq_ui', 'mq_player')}
    assert all(len(pids) == 1 for pids in found.values()), (
        'Expected one live stock UI and player; recreate the disposable stack', found)
    return {name: pids[0] for name, pids in found.items()}


def native_resources():
    pids = guest_processes('disc-service')
    assert len(pids) == 1, pids
    process = Path('/proc')/str(pids[0])
    status = dict(line.split(':', 1) for line in (process/'status').read_text().splitlines())
    return dict(pid=pids[0], qemuVmRSS=status['VmRSS'].strip(), qemuVmSize=status['VmSize'].strip(),
                threads=status['Threads'].strip(), fds=len(list((process/'fd').iterdir())))


def service_log():
    """The gateway's output as the boot layer keeps it (/run/disc-boot/service/log, capped), read
    through the root of a guest process: the guest's /run is its own."""
    for pid in guest_processes('disc-service') + guest_processes('mq_player'):
        try:
            return (Path('/proc')/str(pid)/'root/run/disc-boot/service/log').read_text(errors='replace')
        except OSError:
            continue
    return ''
