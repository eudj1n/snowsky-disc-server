#!/usr/bin/env python3
"""The server's disposable guest: a boot-layer image under stock init, the gateway as its package.

The stack is snowsky-disc-boot's scripts/guest.py (DISC_BOOT_DIR, by default
the sibling checkout) with a record of its own (work/guest.json): the pinned
emulator runs the boot layer's review image on the selected stock firmware,
with /usr/data as a file system, stock's init and its watch loop. The
server's package is staged on the card and the guest powered on holding Play,
so disc-boot installs and starts it as on the player; boot supervises it from
then on. The gateway's ports, 7870 (apps) and 7871 (the manager), are
published on host loopback under the same numbers, so its own authorities
hold; this repository is mounted at /platform for the checks.

  up --reference DIR --image FILE --ota DIR [--package ZIP]
  install [--package ZIP]       power off, stage, power on holding Play, wait until ready
  wait [--confirmed]            the service ready (or confirmed: 180 s after ready)
  restart-service               end the gateway's process; boot starts it again, as after a crash
  power on|reboot|off|cut [--unsynced]|status [--hold KEYS]
  run COMMAND...                in the container
  status | down

Without --package a debug package is built from build/mips (build_package.py
--debug): the checks before a release run the gateway with its debug information.
"""
import argparse
import json
import urllib.request
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BOOT = Path(os.environ.get('DISC_BOOT_DIR', ROOT.parent/'snowsky-disc-boot'))
STATE = ROOT/'work/guest.json'
PORTS = (7870, 7871)


def guest(*args, capture=False):
    if not (BOOT/'scripts/guest.py').is_file():
        raise SystemExit(f'snowsky-disc-boot not found at {BOOT}; set DISC_BOOT_DIR')
    return subprocess.run([sys.executable, str(BOOT/'scripts/guest.py'), '--state', str(STATE), *map(str, args)],
                          check=True, text=True, capture_output=capture)


def status():
    return json.loads(guest('status', capture=True).stdout)


def wait(confirmed=False, limit=None):
    wanted = ('confirmed',) if confirmed else ('ready', 'confirmed')
    until, current = time.monotonic() + (limit or (420 if confirmed else 240)), None
    while time.monotonic() < until:
        current = status()
        service = current.get('service') or {}
        if service.get('state') in wanted:
            return current
        if service.get('state') in ('failed', 'absent', 'stock-mode'):
            break
        time.sleep(3)
    raise SystemExit(f'The service package is not {wanted[0]}: {json.dumps(current)}')


def uptime():
    request = urllib.request.Request(f'http://127.0.0.1:{PORTS[0]}/api/about', headers={'Host': f'127.0.0.1:{PORTS[0]}'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)['service']['uptime']


def restart_service():
    """The gateway's process ends as in a crash; boot restarts a confirmed version after 2 s
    (at most three times in ten minutes)."""
    before = uptime()
    # From the container, by the process name (the guest's BusyBox has no pkill).
    guest('run', 'python3', '-c', 'import os, signal, sys; sys.path.insert(0, "/platform/tests/integration"); '
          'from guest_checks import guest_processes; [os.kill(pid, signal.SIGTERM) for pid in guest_processes("disc-service")]')
    until = time.monotonic() + 120
    while time.monotonic() < until:
        time.sleep(1)
        try:
            if uptime() < before:
                return wait()
        except OSError:
            continue
    raise SystemExit('The gateway did not come back')


def debug_package():
    sys.path.insert(0, str(ROOT/'scripts'))
    import build_package
    output = ROOT/'work'/f'guest-package-{time.time_ns()}'
    return Path(build_package.build(output=output, debug=True)['zip'])


def install(package):
    package = Path(package) if package else debug_package()
    guest('power', 'off')
    guest('stage', '--package', package)
    guest('power', 'on', '--hold', 'play')
    current = wait()
    print(json.dumps({'package': str(package), 'boot': current['boot'], 'service': current['service']}, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    u = sub.add_parser('up')
    u.add_argument('--reference', type=Path, required=True)
    u.add_argument('--image', type=Path, required=True)
    u.add_argument('--ota', type=Path, required=True)
    u.add_argument('--package', type=Path)
    i = sub.add_parser('install')
    i.add_argument('--package', type=Path)
    sub.add_parser('restart-service')
    w = sub.add_parser('wait')
    w.add_argument('--confirmed', action='store_true')
    o = sub.add_parser('power')
    o.add_argument('event', choices=['on', 'reboot', 'off', 'cut', 'status'])
    o.add_argument('--unsynced', action='store_true')
    o.add_argument('--hold', default='')
    r = sub.add_parser('run')
    r.add_argument('command', nargs=argparse.REMAINDER)
    sub.add_parser('status')
    sub.add_parser('down')
    args = p.parse_args()
    if args.action == 'up':
        if STATE.exists():
            p.error('A guest is recorded; use status or down first')
        guest('up', '--reference', args.reference, '--image', args.image, '--ota', args.ota, '--name', 'disc-server-guest',
              *[x for port in PORTS for x in ('--publish', port)], '--mount', f'platform={ROOT}')
        # The checks pin this repository's profile too (firmware_profile.state_profile).
        sys.path.insert(0, str(ROOT/'scripts'))
        from firmware_profile import fingerprint, load_profile
        state = json.loads(STATE.read_text())
        state['firmwareProfileSha256'] = fingerprint(load_profile(state['firmwareVersion']))
        STATE.write_text(json.dumps(state, indent=2) + '\n')
        install(args.package)
        print(f'Apps: http://127.0.0.1:{PORTS[0]}  Manager: http://127.0.0.1:{PORTS[1]}', flush=True)
    elif args.action == 'install':
        install(args.package)
    elif args.action == 'restart-service':
        print(json.dumps(restart_service()['service'], indent=2))
    elif args.action == 'wait':
        print(json.dumps(wait(args.confirmed), indent=2))
    elif args.action == 'power':
        extra = ['--unsynced'] if args.unsynced else []
        guest('power', args.event, '--hold', args.hold, *extra)
        if args.event in ('on', 'reboot'):
            print(json.dumps(wait()['service'], indent=2))
    elif args.action == 'run':
        guest('run', *args.command)
    elif args.action == 'status':
        print(json.dumps(status(), indent=2))
    else:
        guest('down')


if __name__ == '__main__':
    main()
