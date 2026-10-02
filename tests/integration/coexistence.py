"""Short playback/lifecycle observation in the disposable guest; not hardware QA."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import importlib.util
import json
import os
from pathlib import Path
import time
import urllib.request
from emulator.runtime.keys import Buttons, Device
from guest_checks import AUTHORITY, native_resources, stock_processes

assert os.environ.get('CI_DISPOSABLE') == '1'
root=Path('/work/rootfs')
spec=importlib.util.spec_from_file_location('wire','/platform/tests/conformance/test_service.py')
wire=importlib.util.module_from_spec(spec);spec.loader.exec_module(wire)
authority=AUTHORITY
req=urllib.request.Request('http://127.0.0.1:7870/api/health',headers={'Host':authority})
health=json.load(urllib.request.urlopen(req,timeout=3));assert not health['controlActive']

def query(ws,tag,expected,payload=''):
    ws.send(wire.record(tag,payload))
    for _ in range(100):
        op,data=ws.recv();assert op==1,(op,data)
        if data[:4].decode()==expected:
            # a202 also carries asynchronous state-only deltas. This acceptance
            # needs an observed full track, not whichever frame arrived first.
            if expected=='a202' and 'song' not in json.loads(data[8:]):continue
            return data[8:]
    raise AssertionError('Event budget exceeded')

def resources():
    # The gateway's process, run by the boot layer as its package.
    return native_resources()

stock_before=stock_processes()
before=resources();ws=None;positions=[];playing=False
buttons=Buttons(root,Device(root))
try:
    for _ in range(30):
        ws=wire.WS(7870,host=authority,origin='http://'+authority)
        if ws.status==101:break
        ws.close();ws=None;time.sleep(.1)
    assert ws
    assert query(ws,'0599','a599','0000')==IDENTITY.encode()
    assert json.loads(query(ws,'0501','a501'))['soc_version']==MAIN_OS
    assert json.loads(query(ws,'0202','a202'))['state']==1
    # Local player button does not create another TCP owner. Keep the same native
    # connection throughout playback, as a physical owner would do on the player.
    buttons.gesture('play_pause','single');playing=True
    deadline=time.monotonic()+8
    while json.loads(query(ws,'0202','a202'))['state']!=0:
        assert time.monotonic()<deadline,'Local Play was not observed'
        time.sleep(.1)
    audio_before=(root/'audio.pcm').stat().st_size
    for _ in range(6):
        value=json.loads(query(ws,'0202','a202'))
        assert value['state']==0,value
        song=json.loads(value['song']) if isinstance(value.get('song'),str) else value.get('song',{})
        assert song.get('song_name')=='Second — Ё.flac',song
        positions.append(value.get('position',value.get('time')))
        time.sleep(1)
    audio_after=(root/'audio.pcm').stat().st_size
    assert audio_after>audio_before,(audio_before,audio_after)
finally:
    if ws and playing:
        buttons.gesture('play_pause','single');playing=False
        deadline=time.monotonic()+8
        while json.loads(query(ws,'0202','a202'))['state']!=1:
            assert time.monotonic()<deadline,'Local Pause was not observed'
            time.sleep(.1)
    if ws:ws.close()
    for _ in range(100):
        if not json.load(urllib.request.urlopen(req,timeout=3))['controlActive']:break
        time.sleep(.05)
after=resources()
assert stock_processes()==stock_before, 'Stock processes changed during native coexistence'
assert after['fds']==before['fds'],(before,after)
report={'playingReadbacks':6,'pcmBytesAdded':audio_after-audio_before,'restored':'paused','before':before,'after':after,
        'limits':'Short emulator observation; RSS includes QEMU and is not player RAM usage.'}
Path('/work/disc-coexistence.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
