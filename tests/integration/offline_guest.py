"""The player without a network, inside the isolated guest's own network namespace.

Run by tests/integration/offline.py with `ip netns exec <namespace>`: the guest was
powered on with NETWORK=isolated (only loopback), the boot layer started the gateway.

  offline   no eth/wlan; stock plays locally; the gateway serves its page and manager and
            refuses stock routes fast; the evidence goes to /work/disc-offline.json
  arrival   after a wlan0 address came (emulator.runtime.network, outside): stock's
            listeners, an explicit new connection, the same gateway process
"""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import http.client
import json
from pathlib import Path
import subprocess
import sys
import time

from emulator.runtime.keys import Buttons, Device
from research.diagnostics.probe_keys import snapshot as key_snapshot
from tests.integration.guest_check import audio as verify_fixture_pcm
from guest_checks import AUTHORITY, MANAGER_AUTHORITY, MANAGER_PORT, PORT, ROOTFS, native_resources, stock_processes
from handover import connect_native, health, query, released, wire

OUT = Path('/work/disc-offline.json')


def command(*args):
    return subprocess.check_output(args, text=True, timeout=45)


def fetch(path, port=PORT, authority=AUTHORITY):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', path, headers={'Host': authority})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def size():
    path = ROOTFS/'audio.pcm'
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
        return key_snapshot(ROOTFS, VERSION)['player_state']
    except ValueError:
        return None  # Player context has not initialized yet.


def listening():
    listeners = command('ss', '-H', '-lnt')
    return ':12100 ' in listeners and ':12103 ' in listeners


def offline():
    links = json.loads(command('ip', '-j', '-4', 'addr'))
    assert not any(x['ifname'].startswith(('eth', 'wlan')) for x in links), links
    assert not any(x.get('addr_info') for x in links if x['ifname'] != 'lo'), links
    stock = stock_processes()
    native = native_resources()
    assert not listening(), command('ss', '-H', '-lnt')
    assert health()['upstream'] == '127.0.0.1'
    # The gateway serves without a network: its page (the card's app, or the way to the manager),
    # its manager, and the manager's view of the boot layer.
    assert fetch('/')[0] in (200, 302)
    assert fetch('/', MANAGER_PORT, MANAGER_AUTHORITY)[0] == 200
    update = json.loads(fetch('/api/update', MANAGER_PORT, MANAGER_AUTHORITY)[1])
    assert update['running']['name'] == 'disc-server', update
    buttons = Buttons(ROOTFS, Device(ROOTFS))
    wait(lambda: player_state() == 2, 'Prepared track did not restore paused', 60)
    buttons.gesture('play_pause', 'single')
    wait(lambda: player_state() == 1, 'Local Play state not observed')
    wait(lambda: size() > 0, 'Local Play produced no PCM offline')
    before = size()
    started = time.monotonic()
    ws = wire.WS(PORT, host=AUTHORITY, origin='http://'+AUTHORITY)
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
    report = dict(nativePid=native['pid'], stock=stock, offline=dict(
        noEthernetOrWifi=True, stockListeners=False, page=fetch('/')[0], manager=200, boot=update['running'],
        nativeWebSocket=503, nativeCatalog=502, admissionSeconds=round(admission, 2), catalogSeconds=round(catalog, 2),
        pcmBytesAdded=after-before, sourceWaveformMatched=True, localPause=True, stockPidsUnchanged=True))
    OUT.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report), flush=True)


def arrival():
    report = json.loads(OUT.read_text())
    wait(listening, 'Stock listeners did not start after address arrival', 35)
    listeners = command('ss', '-H', '-lnt')
    # The same gateway process and upstream; recovery is an explicit new connection, with
    # stock's identity, firmware, track and HTTP readback.
    assert health()['upstream'] == '127.0.0.1'
    ws = connect_native()
    try:
        assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
        assert json.loads(query(ws, '0501', 'a501'))['soc_version'] == MAIN_OS
        assert json.loads(query(ws, '0202', 'a202'))['state'] == 1  # paused, as the protocol reports it
    finally:
        ws.close()
    released()
    status, body = fetch('/api/catalog')
    assert status == 200 and b'Second' in body, (status, body[:120])
    assert stock_processes() == report['stock']
    assert native_resources()['pid'] == report['nativePid']
    report['addressArrival'] = dict(link='wlan0 192.0.2.2/24', listeners=listeners.splitlines(), handshake=IDENTITY,
                                   firmware=MAIN_OS, pausedReadback=True, nativeCatalog=200,
                                   nativePidUnchanged=True, explicitConnectionRecovery=True)
    OUT.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report['addressArrival']), flush=True)


if __name__ == '__main__':
    {'offline': offline, 'arrival': arrival}[sys.argv[1]]()
