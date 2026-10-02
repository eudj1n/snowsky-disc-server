"""Prepare generated media through the reviewed Python facade, never our bridge.

Run only inside scripts/emulator.py's disposable guest after disconnecting browsers.
"""
import json
import os
from pathlib import Path
import urllib.request
from controller import DeviceConfig, DiscSession
from tests.fixtures.fixture import NAMES
from guest_checks import stock_processes

assert os.environ.get('CI_DISPOSABLE') == '1'
stock_processes()
request = urllib.request.Request('http://127.0.0.1:7870/api/health', headers={'Host':'127.0.0.1:17870'})
health = json.load(urllib.request.urlopen(request, timeout=3))
assert not health['controlActive'], 'Disconnect the browser before alternating ownership'
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
