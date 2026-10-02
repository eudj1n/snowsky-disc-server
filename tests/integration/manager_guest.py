#!/usr/bin/env python3
"""The application manager on the disposable guest (scripts/emulator.py), driven from the host.

Two debug packages of this build carry a test update key made for the run (never the
release key): version 1 is installed with Play and confirmed; through the manager's port
an app is installed, chosen and removed, an installation is refused for want of room on
a nearly full card, then version 2 is uploaded as a signed .update, activated, confirmed
by the boot layer, and rolled back to version 1. Stock's player keeps its processes
throughout. Real timings: each confirmation takes 180 s. Evidence: work/manager-acceptance.json.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'tests/integration'))
import app_bundle  # noqa: E402
import build_package  # noqa: E402
import update_file  # noqa: E402
from guest_checks import MANAGER_AUTHORITY, MANAGER_PORT, SERIAL  # noqa: E402

STATE = json.loads((ROOT/'work/guest.json').read_text())
CONTAINER = STATE['id'] + '-emu'
EMULATOR = [sys.executable, str(ROOT/'scripts/emulator.py')]
evidence = {'steps': []}
counter = iter(range(10000))


def step(name, **facts):
    evidence['steps'].append({'step': name, **facts})
    print(f'[manager-guest] {name}: {json.dumps(facts)[:400]}', flush=True)


def manager(method, path, body=None, change=False, content_type='application/json'):
    headers = {'Host': MANAGER_AUTHORITY}
    if change:
        headers.update({'X-Disc-Token': SERIAL, 'X-Disc-Request': f'guest-{time.time_ns()}-{next(counter)}', 'Content-Type': content_type})
    request = urllib.request.Request(f'http://127.0.0.1:{MANAGER_PORT}{path}', data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.loads(response.read() or b'null')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode().strip()


def in_container(*command):
    return subprocess.run(['docker', 'exec', CONTAINER, *command], check=True, capture_output=True, text=True).stdout


def stock():
    return json.loads(in_container('python3', '-c', 'import sys, json; sys.path.insert(0, "/platform/tests/integration"); '
                                   'from guest_checks import guest_processes; '
                                   'print(json.dumps({n: guest_processes(n) for n in ("mq_ui", "mq_player")}))'))


def service_state():
    return json.loads(subprocess.run(EMULATOR + ['status'], check=True, capture_output=True, text=True).stdout)['service']


def wait_version(version, confirmed=True, limit=480):
    until, current = time.monotonic() + limit, None
    while time.monotonic() < until:
        try:
            current = service_state()
        except subprocess.CalledProcessError:
            current = None
        if current and current['version'] == version and (current['state'] == 'confirmed' if confirmed else current['state'] in ('ready', 'confirmed')):
            return current
        time.sleep(5)
    raise AssertionError(f'version {version} not {"confirmed" if confirmed else "ready"}: {current}')


def main():
    work = Path(tempfile.mkdtemp(prefix='manager-guest-', dir=ROOT/'work'))
    key, keys = work/'update.key', work/'update-keys'
    keys.write_text(update_file.keygen(key) + '\n')
    stamp = time.strftime('%Y.%m.%d')
    first = build_package.build(output=work/'v1', version=f'{stamp}-guest1', update_keys=keys, debug=True)
    second = build_package.build(output=work/'v2', version=f'{stamp}-guest2', update_keys=keys, sign_key=key, debug=True)
    v1, v2 = first['version'], second['version']
    subprocess.run(EMULATOR + ['install', '--package', first['zip']], check=True)
    step('installed with play', service=wait_version(v1))
    players = stock()

    # Apps: install, choose, remove.
    source = work/'radio-src'
    source.mkdir()
    (source/'index.html').write_text('<!doctype html><title>Radio</title><script type="module" src="./app.js"></script>')
    (source/'app.js').write_text('document.title = "Radio"')
    radio = work/'radio.zip'
    app_bundle.zip_app(app_bundle.build_app(source, 'Radio', '1.0.0'), 'Radio', radio)
    status, answer = manager('POST', '/api/apps', radio.read_bytes(), change=True, content_type='application/zip')
    assert status == 200 and answer['name'] == 'Radio', (status, answer)
    listing = manager('GET', '/api/apps')[1]
    assert any(app['name'] == 'Radio' and app['version'] == '1.0.0' for app in listing['apps']), listing
    assert manager('PUT', '/api/apps/default', json.dumps({'name': 'Radio'}).encode(), change=True)[1]['default'] == 'Radio'
    assert in_container('cat', '/tmp/sdcard/Apps/Radio/index.html').startswith('<!doctype html>')
    status, listing = manager('DELETE', '/api/apps/Radio', b'', change=True)
    assert status == 200 and listing['chosen'] is None and all(a['name'] != 'Radio' for a in listing['apps']), listing
    step('app installed, chosen and removed', installed=answer, after=listing)

    # Room: a card nearly full refuses an app and keeps what it has.
    free = int(in_container('sh', '-c', "df -kP /tmp/sdcard | awk 'NR==2 {print $4}'")) * 1024
    filler = max(free - 4 * 1024 * 1024, 0)
    in_container('sh', '-c', f'head -c {filler} /dev/zero > /tmp/sdcard/.room-filler && sync')
    try:
        status, problem = manager('POST', '/api/apps', radio.read_bytes(), change=True, content_type='application/zip')
        assert status == 507 and 'Not enough free space' in problem, (status, problem)
        assert not in_container('sh', '-c', 'ls -A /tmp/sdcard/Apps | grep -c Radio || true').strip().strip('0')
    finally:
        in_container('sh', '-c', 'rm -f /tmp/sdcard/.room-filler && sync')
    step('no room', status=status, problem=problem)

    # The server's update: upload, activate, confirmation, rollback.
    status, staged = manager('POST', '/api/update', Path(second['update']).read_bytes(), change=True, content_type='application/octet-stream')
    assert status == 200 and staged['version'] == v2, (status, staged)
    update = manager('GET', '/api/update')[1]
    assert update['staged'] == {'name': 'disc-server', 'version': v2} and update['running']['version'] == v1, update
    status, answer = manager('POST', '/api/update/activate', b'', change=True)
    assert status == 202 and answer == {'restarting': True, 'version': v2}, (status, answer)
    started = time.monotonic()
    tentative = wait_version(v2, confirmed=False)
    current = wait_version(v2)
    update = manager('GET', '/api/update')[1]
    assert update['previous'] == {'name': 'disc-server', 'version': v1} and update['staged'] is None, update
    assert current['lastRequest'] == f'activated disc-server {v2}', current
    step('activated and confirmed', tentative=tentative, service=current, seconds=round(time.monotonic() - started))
    status, answer = manager('POST', '/api/update/rollback', b'', change=True)
    assert status == 202 and answer == {'restarting': True, 'version': v1}, (status, answer)
    current = wait_version(v1)
    assert current['lastRequest'] == f'rolled back to disc-server {v1}', current
    update = manager('GET', '/api/update')[1]
    assert update['running']['version'] == v1 and update['staged'] == {'name': 'disc-server', 'version': v2}, update
    step('rolled back', service=current, update=update)
    assert stock() == players, 'stock restarted while the server was updated'
    evidence['status'] = 'passed'
    evidence['stock'] = players


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        evidence['status'] = f'failed: {error}'
        raise
    finally:
        (ROOT/'work/manager-acceptance.json').write_text(json.dumps(evidence, indent=2, default=str) + '\n')
