"""Compare immediate raw/native handover and verify explicit recovery, no replay.

An initial stock reset is an observed firmware limitation, not a passing seamless
handover. Every row records that outcome separately from the recovery connection.
"""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import importlib.util
import json
from pathlib import Path
import socket
import struct
import time
import urllib.request

from controller import DeviceConfig, DiscSession
from controller.fiio_link import Frames, frame
from guest_checks import stock_processes

spec = importlib.util.spec_from_file_location('wire', '/platform/tests/conformance/test_service.py')
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
AUTHORITY = '127.0.0.1:17870'


def health():
    req = urllib.request.Request('http://127.0.0.1:7870/api/health', headers={'Host': AUTHORITY})
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def released():
    deadline = time.monotonic()+5
    while health()['controlActive']:
        assert time.monotonic() < deadline, 'Native owner reservation leaked'
        time.sleep(.03)


def connect_native():
    ws = wire.WS(7870, host=AUTHORITY, origin='http://'+AUTHORITY)
    if ws.status != 101:
        ws.close()
        raise AssertionError(('Native admission failed', ws.status))
    return ws


def query(ws, tag, expected, payload=''):
    ws.send(frame(tag, payload))
    deadline = time.monotonic()+5
    for _ in range(100):
        remaining = deadline-time.monotonic()
        assert remaining > 0, 'Native query deadline exceeded'
        ws.socket.settimeout(remaining)
        op, data = ws.recv()
        if op == 8 and data == struct.pack('!H', 1011):
            raise ConnectionResetError('Native upstream close 1011')
        assert op == 1, (op, data)
        if data[:4].decode() == expected:
            if expected == 'a202' and 'song' not in json.loads(data[8:]):
                continue
            return data[8:]
    raise AssertionError('Event budget exceeded')


def raw_handshake(host):
    deadline = time.monotonic()+3
    while True:
        try:
            peer = socket.create_connection((host, 12100), timeout=3)
            break
        except ConnectionRefusedError:
            assert time.monotonic() < deadline, 'Stock listener did not return'
            time.sleep(.1)  # Admission only; no application byte sent yet.
    with peer:
        peer.sendall(frame('0599', '0000'))
        parser = Frames()
        deadline = time.monotonic()+5
        for _ in range(100):
            remaining = deadline-time.monotonic()
            assert remaining > 0, 'Raw handshake deadline exceeded'
            peer.settimeout(remaining)
            data = peer.recv(4096)
            assert data, 'Unexpected EOF instead of handshake/reset'
            for tag, payload in parser.feed(data):
                if tag == 'a599':
                    assert payload == IDENTITY.encode(), payload
                    return
        raise AssertionError('Event budget exceeded')


def run():
    stock = stock_processes()
    initial = health()
    assert not initial['controlActive'], 'Disconnect browser before handover tests'
    config = DeviceConfig(initial['upstream'])
    rows = []
    try:
        for mode in ('native', 'raw', 'native', 'raw'):
            # These mutations use the reviewed reference, only on disposable media.
            with DiscSession(config) as session:
                session.connect()
                assert session.wait_ready(15), session.status()
                # Each attempt starts the two-track CI album from its first 30 s track, so the
                # queue never runs out however slow the run (it did in two runs with resume).
                assert session.play_album('CI Album', index=0).to_dict()['status'] == 'playing'
            start = time.monotonic()
            ws = None
            outcome = 'handshake'
            try:
                if mode == 'native':
                    ws = connect_native()
                    assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
                else:
                    raw_handshake(initial['upstream'])
            except ConnectionResetError:
                outcome = 'stock_reset'
            finally:
                if ws:
                    ws.close()
            elapsed = time.monotonic()-start
            released()
            # A separate, deliberate Connect after the first connection ended.
            # Failure here fails the test; there is no handshake retry loop.
            recovery = connect_native()
            try:
                assert query(recovery, '0599', 'a599', '0000') == IDENTITY.encode()
                assert json.loads(query(recovery, '0501', 'a501'))['soc_version'] == MAIN_OS
                playback = json.loads(query(recovery, '0202', 'a202'))
                assert playback['state'] == 0, playback
                assert json.loads(playback['song'])['song_name'] in ('Second — Ё.flac', 'Third — й.flac')
            finally:
                recovery.close()
            released()
            assert stock_processes() == stock, 'Stock process restarted during handover'
            row = dict(transport=mode, immediate=outcome, elapsedMs=round(elapsed*1000),
                       explicitRecovery='playing', stockProcessesUnchanged=True)
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        released()
        with DiscSession(config) as session:
            session.connect()
            assert session.wait_ready(15), session.status()
            assert session.pause().to_dict()['status'] in ('confirmed', 'already_satisfied')
        assert stock_processes() == stock
    report = {'attempts': rows, 'restored': 'paused', 'stockPids': stock,
              'scope': 'Initial reset is recorded, not hidden; only explicit new connections recover.'}
    Path('/work/disc-handover.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    run()
