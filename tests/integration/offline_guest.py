"""Stock/native behavior with no Ethernet/Wi-Fi interface, then isolated address arrival."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import http.client
import json
from pathlib import Path
import subprocess
import time

from emulator.runtime.keys import Buttons, Device
from research.diagnostics.probe_keys import snapshot as key_snapshot
from tests.integration.guest_check import audio as verify_fixture_pcm
from guest_checks import native_resources, stock_processes
from handover import AUTHORITY, connect_native, health, query, released, wire

ROOT = Path('/work/rootfs')


def command(*args):
    return subprocess.check_output(args, text=True, timeout=45)


def fetch(path):
    connection = http.client.HTTPConnection('127.0.0.1', 7870, timeout=5)
    try:
        connection.request('GET', path, headers={'Host': AUTHORITY})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def size():
    path = ROOT/'audio.pcm'
    return path.stat().st_size if path.exists() else 0


def wait(predicate, label, seconds=12):
    deadline = time.monotonic()+seconds
    while not predicate():
        assert time.monotonic() < deadline, label
        time.sleep(.1)


def player_state():
    # Fingerprinted read-only diagnostic; never patch stock memory. First frame
    # readiness can precede restoration of the selected track after boot.
    try:
        return key_snapshot(ROOT, VERSION)['player_state']
    except ValueError:
        return None  # Player context has not initialized yet.


def run():
    links = json.loads(command('ip', '-j', '-4', 'addr'))
    assert not any(x['ifname'].startswith(('eth', 'wlan')) for x in links), links
    assert not any(x.get('addr_info') for x in links if x['ifname'] != 'lo'), links
    stock = stock_processes()
    native = native_resources()
    listeners = command('ss', '-H', '-lnt')
    assert ':12100 ' not in listeners and ':12103 ' not in listeners, listeners
    assert health()['upstream'] == '127.0.0.1'
    assert fetch('/')[0] == 200
    buttons = Buttons(ROOT, Device(ROOT))
    wait(lambda: player_state() == 2, 'Prepared track did not restore paused', 20)
    buttons.gesture('play_pause', 'single')
    wait(lambda: player_state() == 1, 'Local Play state not observed')
    wait(lambda: size() > 0, 'Local Play produced no PCM offline')
    before = size()
    started = time.monotonic()
    ws = wire.WS(7870, host=AUTHORITY, origin='http://'+AUTHORITY)
    try:
        assert ws.status == 503, ws.status
    finally:
        ws.close()
    admission = time.monotonic()-started
    assert admission < 4.5, admission
    started = time.monotonic()
    assert fetch('/api/catalog')[0] == 502
    catalog = time.monotonic()-started
    assert catalog < 4.5, catalog
    assert not health()['controlActive']
    wait(lambda: size() > before, 'Playback stopped during unavailable native reads')
    after = size()
    assert player_state() == 1
    verify_fixture_pcm()
    buttons.gesture('play_pause', 'single')
    wait(lambda: player_state() == 2, 'Local Pause state not observed')
    assert stock_processes() == stock
    assert native_resources()['pid'] == native['pid']
    report = dict(offline=dict(noEthernetOrWifi=True, stockListeners=False,
                  nativeAssets=200, nativeWebSocket=503, nativeCatalog=502,
                  admissionSeconds=round(admission, 2), catalogSeconds=round(catalog, 2),
                  pcmBytesAdded=after-before, sourceWaveformMatched=True,
                  localPause=True, stockPidsUnchanged=True))
    print(json.dumps(report), flush=True)
    # This dummy link exists only in the test namespace: no host/Docker routing
    # changes and no real LAN. A normal address event exercises stock startup.
    command('ip', 'link', 'add', 'eth1', 'type', 'dummy')
    command('ip', 'link', 'set', 'eth1', 'up')
    command('ip', 'addr', 'add', '192.0.2.2/24', 'dev', 'eth1')
    command('ip', 'route', 'add', 'default', 'via', '192.0.2.1', 'dev', 'eth1')
    command('bash', '/repo/emulator/scripts/16_network.sh', 'prepare')
    wait(lambda: ':12100 ' in command('ss', '-H', '-lnt') and ':12103 ' in command('ss', '-H', '-lnt'),
         'Stock listeners did not start after address arrival', 35)
    listeners = command('ss', '-H', '-lnt')
    print('Recovered listeners:', listeners, flush=True)
    # Keep the exact same native process/upstream. A successful upgrade alone
    # is insufficient: require stock identity, firmware, track and HTTP readback.
    assert health()['upstream'] == '127.0.0.1'
    ws = connect_native()
    try:
        assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
        assert json.loads(query(ws, '0501', 'a501'))['soc_version'] == MAIN_OS
        assert json.loads(query(ws, '0202', 'a202'))['state'] == 1
    finally:
        ws.close()
    released()
    status, body = fetch('/api/catalog')
    assert status == 200
    assert b'Second' in body
    assert stock_processes() == stock
    assert native_resources()['pid'] == native['pid']
    report['addressArrival'] = dict(listeners=listeners, handshake=IDENTITY, firmware=MAIN_OS,
                                   pausedReadback=True, nativeCatalog=200,
                                   nativePidUnchanged=True, explicitConnectionRecovery=True)
    Path('/work/disc-offline.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
