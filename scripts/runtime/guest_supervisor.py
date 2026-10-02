#!/usr/bin/env python3
"""Viewer-free adapter to the reference guest's power-request consumer.

Runs outside the chroot in the recorded disposable container. Never boots,
restarts, touches, polls the stock protocol or changes the firmware idle policy.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

SCRIPT = '/platform/scripts/runtime/guest_supervisor.py'
READY = Path('/work/disc-supervisor.json')
LOCK = Path('/work/disc-supervisor.lock')
LOG = Path('/work/disc-supervisor.log')


def supervise(device, stop, lifetime=7200, clock=time.monotonic, sleep=time.sleep):
    deadline = clock()+lifetime
    previous = None
    while not stop.is_set() and clock() < deadline:
        device.service_requests()
        if device.error:
            raise RuntimeError(device.error)
        current = device.transition
        if current != previous:
            print('Power request: '+(current or 'completed'), flush=True)
            previous = current
        stop.wait(min(.25, max(0, deadline-clock())))
    # The reference stops asynchronously. Do not abandon that stop halfway or
    # allow an old transition to signal processes from a subsequent explicit boot.
    deadline = clock()+5
    while device.transition:
        if clock() >= deadline:
            raise TimeoutError('Guest shutdown did not finish before supervisor exit')
        sleep(.25)
    if device.error:
        raise RuntimeError(device.error)


def processes(proc=Path('/proc')):
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal(): continue
        try:
            argv = (entry/'cmdline').read_bytes().split(b'\0')
            if (argv[1:] == [b'-B', SCRIPT.encode(), b'run', b'']
                    and (entry/'root').resolve(strict=True) == Path('/')
                    and (entry/'stat').read_text().split(') ', 1)[1][0] != 'Z'):
                found.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return found


def ready():
    pids = processes()
    assert len(pids) <= 1, ('Multiple supervisors', pids)
    try:
        value = json.loads(READY.read_text())
    except (OSError, ValueError):
        return None
    return value if value.get('pid') in pids else None


def start():
    current = ready()
    if current:
        print(json.dumps(current), flush=True)
        return
    with LOG.open('ab') as output:
        child = subprocess.Popen([sys.executable, '-B', SCRIPT, 'run'],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 start_new_session=True)
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        value = ready()
        if value:
            print(json.dumps(value), flush=True)
            return
        if child.poll() is not None:
            raise RuntimeError('Supervisor failed to start; inspect '+str(LOG))
        time.sleep(.05)
    raise TimeoutError('Supervisor readiness not observed')


def stop():
    for pid in processes():
        # Exact command/chroot membership rechecked immediately before signaling.
        if pid in processes():
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
    deadline = time.monotonic()+8
    while processes():
        if time.monotonic() >= deadline:
            raise TimeoutError('Supervisor did not stop; refusing a competing boot')
        time.sleep(.05)


def run():
    # Keep the dependency external and import it only in the confined runtime.
    from emulator.runtime.keys import Device
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop_event = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop_event.set())
        value = dict(pid=os.getpid(), root='/work/rootfs', lifetimeSeconds=7200)
        READY.write_text(json.dumps(value)+'\n')
        print('Supervisor ready: '+json.dumps(value), flush=True)
        try:
            supervise(Device(value['root']), stop_event)
        finally:
            READY.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status', 'run'])
    args = parser.parse_args()
    if os.environ.get('CI_DISPOSABLE') != '1' or Path(__file__).resolve() != Path(SCRIPT):
        parser.error('Recorded disposable container required')
    if args.action == 'run':
        run()
    elif args.action == 'status':
        print(json.dumps(ready()))
    else:
        # Serialize management calls separately from the daemon's lifetime lock.
        with Path('/work/disc-supervisor-control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            start() if args.action == 'start' else stop()


if __name__ == '__main__': main()
