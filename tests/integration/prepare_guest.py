"""Prepare generated media through the reviewed Python facade, never our bridge.

Run only inside scripts/emulator.py's disposable guest after disconnecting browsers.
"""
import json
import os
import time
from pathlib import Path
import urllib.request
from controller import DeviceConfig, DiscSession
from tests.fixtures.fixture import NAMES
from guest_checks import AUTHORITY, PORT, stock_processes

assert os.environ.get('CI_DISPOSABLE') == '1'
stock_processes()
request = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/health', headers={'Host': AUTHORITY})
health = json.load(urllib.request.urlopen(request, timeout=3))
assert not health['controlActive'], 'Disconnect the browser before alternating ownership'
# After stock's watch loop restarts the pair, the guest's card is mounted again a moment later.
for _ in range(60):
    if all((Path('/work/rootfs/tmp/sdcard')/'Кириллица Ё й'/name).exists() for name in NAMES):
        break
    time.sleep(1)
for name in NAMES:
    relative=Path('Кириллица Ё й')/name
    assert (Path('/work/rootfs/tmp/sdcard')/relative).read_bytes() == (Path('/sdcard')/relative).read_bytes()
with DiscSession(DeviceConfig(health['upstream'])) as session:
    session.connect()
    assert session.wait_ready(15), session.status()
    result=session.scan_library(timeout=45)
    print('Scan:',result.to_dict(),flush=True)
    assert result.to_dict()['status'] == 'confirmed'
    result=session.play_album('CI Album', index=0)
    print('Select:',result.to_dict(),flush=True)
    assert result.to_dict()['status'] == 'playing'
    result=session.pause()
    print('Pause:',result.to_dict(),flush=True)
    assert result.to_dict()['status'] in ('confirmed', 'already_satisfied')
    assert session.snapshot().playback.track is not None
print('Fixture prepared; Python control owner released.',flush=True)
stock_processes()
