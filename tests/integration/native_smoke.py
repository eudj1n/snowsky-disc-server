"""Host-side read-only checks against the native MIPS service in the guest."""
from selected_firmware import VERSION, MAIN_OS, IDENTITY

import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('native_test_wire',ROOT/'tests/conformance/test_service.py')
wire=importlib.util.module_from_spec(spec);spec.loader.exec_module(wire)
from guest_checks import PORT  # noqa: E402

def get(path):
    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}'+path, timeout=5) as r:return json.load(r)

def released():
    for _ in range(100):
        if not get('/api/health')['controlActive']:return
        time.sleep(.05)
    raise AssertionError('Control reservation leaked')

def query(ws, tag, expected, payload=''):
    ws.send(wire.record(tag,payload));events=[]
    for _ in range(100):
        op, data=ws.recv()
        assert op==1,(op,data)
        assert int(data[4:8],16)==len(data)
        if data[:4].decode()==expected:
            if expected=='a202' and 'song' not in json.loads(data[8:]):continue
            return data[8:].decode(),events
        events.append(data[:4].decode())
    raise AssertionError('Event budget exceeded')

health=get('/api/health');assert not health['controlActive'],'Disconnect browser first'
ws=wire.WS(PORT,origin=f'http://127.0.0.1:{PORT}')
if ws.status != 101:
    status=ws.status;body=ws.stream.read(4096);ws.close()
    raise AssertionError(f'Initial upgrade failed: HTTP {status}: {body!r}')
try:
    handshake,_=query(ws,'0599','a599','0000');assert handshake==IDENTITY
    raw,events=query(ws,'0501','a501');settings=json.loads(raw);assert settings['soc_version']==MAIN_OS
    raw,_=query(ws,'0202','a202');playback=json.loads(raw)
    song=json.loads(playback['song']) if isinstance(playback.get('song'),str) else playback.get('song',{})
    assert playback['state'] in (0,1) and song.get('song_name'),playback
    conflict=wire.WS(PORT)
    try:assert conflict.status==409,conflict.status
    finally:conflict.close()
    catalog=get('/api/catalog');assert isinstance(catalog,list) and len(catalog)==3,catalog
    pages=[];pagination=[]
    for start in range(4):
        request=urllib.request.Request(f'http://127.0.0.1:{PORT}/api/catalog/stream',
                                       headers={'start-pos':str(start),'num-max':'1'})
        with urllib.request.urlopen(request,timeout=5) as reply:
            assert reply.headers.get('Transfer-Encoding')=='chunked',dict(reply.headers)
            assert reply.headers.get('total-num')=='3',dict(reply.headers)
            page=json.load(reply)
            assert isinstance(page,list) and len(page)==(1 if start<3 else 0),page
            pages.extend(page);pagination.append(dict(start=start,rows=len(page),
                total=reply.headers.get('total-num'),mark=reply.headers.get('mark-pos')))
    assert pages==catalog,(pages,catalog)
    ws.send('0502000C0078')
    while True:
        op, data=ws.recv()
        if op==8:assert data[:2]==b'\x03\xf0';break
finally:ws.close()
released()
for _ in range(5):
    # Stock reopens its listener asynchronously. Retry connection only.
    for attempt in range(30):
        ws=wire.WS(PORT)
        if ws.status==101:break
        assert ws.status==503,ws.status;ws.close();time.sleep(.1)
    else:raise AssertionError('Stock listener did not return')
    try:assert query(ws,'0599','a599','0000')[0]==IDENTITY
    finally:ws.close()
    released()
report={'firmware':settings['soc_version'],'handshake':handshake,'playbackState':playback['state'],
        'syntheticTitle':song['song_name'],'catalogRows':len(catalog),'secondClient':409,
        'mutationRejected':1008,'reconnections':5,'controlReleased':True,'upstream':health['upstream'],
        'streamingPages':pagination}
out=ROOT/'work/native-smoke.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
