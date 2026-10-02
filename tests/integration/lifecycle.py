"""Two-minute native session and deliberate stock loss, only in disposable CI.

Run through scripts/integration.py --lifecycle, which reboots the disposable
guest and verifies recovery in a finally block even if this scenario fails.
"""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import json
import os
from pathlib import Path
import signal
import struct
import time
import urllib.error
import urllib.request

from guest_checks import native_resources, stock_processes
from handover import AUTHORITY, connect_native, health, query, released, wire


def catalog_status():
    req = urllib.request.Request('http://127.0.0.1:7870/api/catalog', headers={'Host': AUTHORITY})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            assert len(json.load(response)) == 3
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def run():
    stock = stock_processes()
    assert not health()['controlActive'], 'Disconnect browser before lifecycle test'
    before = native_resources()
    ws = connect_native()
    samples = []
    count = 0
    started = time.monotonic()
    try:
        assert query(ws, '0599', 'a599', '0000') == IDENTITY.encode()
        assert json.loads(query(ws, '0501', 'a501'))['soc_version'] == MAIN_OS
        while time.monotonic()-started < 120:
            state = json.loads(query(ws, '0202', 'a202'))
            assert state['state'] == 1, state
            if count % 5 == 0:
                assert catalog_status() == 200
                samples.append(native_resources())
                assert stock_processes() == stock
            count += 1
            time.sleep(2)  # Observation cadence, never command retry/pacing.
        # Exact PID from the verified rootfs; no process-name-wide kill.
        assert stock_processes() == stock
        os.kill(stock['mq_player'], signal.SIGTERM)
        deadline = time.monotonic()+5
        for _ in range(100):
            remaining = deadline-time.monotonic()
            assert remaining > 0, 'Stock loss close deadline exceeded'
            ws.socket.settimeout(remaining)
            op, data = ws.recv()
            if op == 8:
                assert data == struct.pack('!H', 1011), data
                break
        else:
            raise AssertionError('Stock loss did not close WS')
    finally:
        ws.close()
    released()
    unavailable = wire.WS(7870, host=AUTHORITY)
    try:
        assert unavailable.status == 503, unavailable.status
    finally:
        unavailable.close()
    assert catalog_status() == 502
    assert health()['controlActive'] is False
    after = native_resources()
    assert after['pid'] == before['pid'], 'Native service restarted unexpectedly'
    assert after['fds'] == before['fds'], (before, after)
    assert after['threads'] == before['threads'], (before, after)
    assert max(s['fds'] for s in samples)-min(s['fds'] for s in samples) <= 1, samples
    report = dict(durationSeconds=round(time.monotonic()-started, 1), readbacks=count,
                  catalogReads=len(samples), before=before, samples=samples, after=after,
                  stockLossWsCode=1011, unavailableWsStatus=503, unavailableHttpStatus=502,
                  limits='Paused two-minute emulator observation, not hardware memory or long soak acceptance.')
    Path('/work/disc-lifecycle.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    run()
