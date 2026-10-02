#!/usr/bin/env python3
"""The service package under the boot program on the disposable V2.57 guest, beside stock.

Runs on the host against a running emulator stack (by default the one recorded in
the sibling snowsky-disc-web's work/emulator.json). Stops the stack's own
companion, places the MIPS disc-boot and the package's slot into the guest
rootfs, runs `disc-boot early` and `start` as the boot layer's hooks do (keys are
unreadable there: the default mode), waits for the real 180 s confirmation,
checks the gateway against stock's data and diagnostics, stops it through the
boot program and restores the stack. The guest's stock init and the image's
hooks are not exercised (the emulator starts stock itself).
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BOOT = Path(os.environ.get('DISC_BOOT_DIR', ROOT.parent/'snowsky-disc-boot'))
GUEST = '/work/rootfs'
PROBE = r'''
import http.client, json, sys
path = sys.argv[1]
c = http.client.HTTPConnection('127.0.0.1', 7870, timeout=10)
c.request('GET', path, headers={'Host': '127.0.0.1:7870'})
r = c.getresponse()
print(json.dumps({'status': r.status, 'body': r.read().decode('utf-8', 'replace')}))
'''


def container(state):
    return json.loads(Path(state).read_text())['id'] + '-emu'


def sh(name, script, check=True, timeout=120):
    return subprocess.run(['docker', 'exec', '-e', 'CI_DISPOSABLE=1', name, 'sh', '-c', script],
                          capture_output=True, text=True, check=check, timeout=timeout)


def get(name, path):
    out = subprocess.run(['docker', 'exec', name, 'python3', '-c', PROBE, path], capture_output=True, text=True, timeout=30, check=True)
    answer = json.loads(out.stdout)
    return answer['status'], answer['body']


def wait_status(name, state, limit):
    until = time.monotonic() + limit
    last = None
    while time.monotonic() < until:
        out = sh(name, f'cat {GUEST}/run/disc-boot/service.json 2>/dev/null', check=False)
        try:
            last = json.loads(out.stdout)
            if last['state'] == state:
                return last
        except (json.JSONDecodeError, KeyError):
            pass
        time.sleep(2)
    raise AssertionError(f'service never reached {state}: {last}')


def run(state, package_zip, boot_binary, output):
    name = container(state)
    work = Path(output)
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # The stack's own companion holds port 7870: stop it (restored at the end).
    sh(name, 'python3 /platform/scripts/stop-service.py')
    try:
        sh(name, f'rm -rf {GUEST}/opt/disc-boot {GUEST}/usr/data/disc-boot {GUEST}/run/disc-boot && mkdir -p {GUEST}/opt/disc-boot {GUEST}/usr/data/disc-boot/service/a')
        subprocess.run(['docker', 'cp', str(boot_binary), f'{name}:{GUEST}/opt/disc-boot/disc-boot'], check=True)
        with zipfile.ZipFile(package_zip) as z:
            names = z.namelist()
        subprocess.run(['docker', 'cp', str(package_zip), f'{name}:/work/package.zip'], check=True)
        sh(name, f'cd {GUEST}/usr/data/disc-boot/service/a && python3 -c "import zipfile,os,stat; z=zipfile.ZipFile(\'/work/package.zip\'); '
                 f'[ (z.extract(i), os.chmod(i.filename, stat.S_IMODE(i.external_attr>>16) or 0o644)) for i in z.infolist() ]" '
                 f'&& chmod 755 {GUEST}/opt/disc-boot/disc-boot && rm /work/package.zip')
        sh(name, f'printf \'{{"schema":1,"current":"a","confirmed":false,"previous":null}}\' > {GUEST}/usr/data/disc-boot/service/state.json')
        verify = sh(name, f'chroot {GUEST} /opt/disc-boot/disc-boot verify service /usr/data/disc-boot/service/a --profile 2.57', check=False)
        assert json.loads(verify.stdout)['ok'], verify.stdout
        sh(name, f'chroot {GUEST} /opt/disc-boot/disc-boot early --profile 2.57 --card /tmp/sdcard --card-source /dev/mmcblk0p1')
        decision = json.loads(sh(name, f'cat {GUEST}/run/disc-boot/boot.json').stdout)
        assert (decision['mode'], decision['reason'], decision['keys']['read']) == ('platform', 'default', False), decision
        sh(name, f'chroot {GUEST} /opt/disc-boot/disc-boot start')
        ready = wait_status(name, 'ready', 60)
        confirmed = wait_status(name, 'confirmed', 240)
        confirmed_after = round(time.monotonic() - started, 1)
        status, body = get(name, '/api/health')
        health = json.loads(body)
        assert status == 200 and health['store'] and health['media'], body
        status, body = get(name, '/api/about')
        about = json.loads(body)
        assert status == 200 and about['boot']['service']['state'] == 'confirmed', body
        assert about['boot']['service']['version'] == confirmed['version'], body
        status, body = get(name, '/api/data/library_summary')
        assert status == 200, body
        summary = json.loads(body)
        state_file = json.loads(sh(name, f'cat {GUEST}/usr/data/disc-boot/service/state.json').stdout)
        assert state_file == {'schema': 1, 'current': 'a', 'confirmed': True, 'previous': None}, state_file
        sh(name, f'chroot {GUEST} /opt/disc-boot/disc-boot stop', timeout=60)
        stopped = wait_status(name, 'stopped', 30)
        assert sh(name, f'test -e {GUEST}/run/disc-boot/supervisor.pid', check=False).returncode != 0
        result = dict(status='passed', package=confirmed['name'], version=confirmed['version'], files=len(names),
                      decision=decision['reason'], readyState=ready['state'], confirmedAfterSeconds=confirmed_after,
                      health={k: health[k] for k in ('store', 'media', 'history') if k in health},
                      libraryRows=summary.get('rows', summary)[:1] if isinstance(summary.get('rows', summary), list) else summary.get('rows'),
                      stopped=stopped['state'] == 'stopped',
                      scope='MIPS disc-boot and the package in the V2.57 guest beside stock; no stock init, image hooks, keys or device')
        (work/'package-guest.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))
    finally:
        sh(name, f'chroot {GUEST} /opt/disc-boot/disc-boot stop', check=False, timeout=60)
        sh(name, f'rm -rf {GUEST}/opt/disc-boot {GUEST}/usr/data/disc-boot {GUEST}/run/disc-boot', check=False)
        sh(name, 'bash /platform/scripts/guest-service.sh >/dev/null 2>&1', check=False, timeout=180)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack', type=Path, default=ROOT.parent/'snowsky-disc-web/work/emulator.json')
    p.add_argument('--package', type=Path, required=True, help='The MIPS package zip (scripts/build_package.py)')
    p.add_argument('--boot', type=Path, default=BOOT/'build/mips/disc-boot')
    p.add_argument('--output', type=Path, default=ROOT/'work')
    args = p.parse_args()
    run(args.stack, args.package, args.boot, args.output)
