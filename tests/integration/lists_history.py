"""Guest acceptance of combined-009's routes on stock V2.57, generated media only.

An M3U list is written through the service with the player's serial number, read back, played through the
gateway's control channel (the card catalog admits folder play with the list's path), switched to its second
entry with the seek sent right after the selection, replaced and deleted. A play a page sounded itself is
recorded and served with source "browser", and the health's history write state stays "ok". Everything
generated is removed and the library rescanned; evidence goes to /work/disc-lists.json.
"""
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from controller.fiio_link import frame  # noqa: E402
from gateway_mutation import AUTHORITY, PORT, call, wait_event  # noqa: E402
from m3u_playback import SERIAL, tone  # noqa: E402
from test_service import WS  # noqa: E402
from trash_card import scan  # noqa: E402

CARD = Path('/tmp/sdcard')
FOLDER = CARD/'Lists Acceptance'
LISTS = CARD/'.disc'/'playlists'
NAME = 'Вечер · Mix'
OUT = Path('/work/disc-lists.json')
TITLES = ('Evening One', 'Evening Two', 'Evening Three')
evidence = {}
counter = [0]


def note(name, value):
    evidence[name] = value
    print(name, json.dumps(value, ensure_ascii=False)[:500], flush=True)


def guarded(extra=None):
    counter[0] += 1
    return {'X-Disc-Token': SERIAL, 'X-Disc-Request': f'lists-acceptance-{int(time.time())}-{counter[0]:03d}', **(extra or {})}


def lists(method, name=None, body=None, scope='internal'):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = guarded({'Content-Length': str(len(data))} if data is not None else {}) if method != 'GET' else {}
    status, raw, _ = call(method, f'/api/lists/{scope}' + ('' if name is None else '/' + quote(name, safe='')), headers, data)
    return status, json.loads(raw) if raw.startswith(b'{') else raw


def track(n):
    return FOLDER/f'{n + 1} {TITLES[n]}.flac'


def song(payload):
    outer = json.loads(payload[8:])
    return json.loads(outer['song']) if isinstance(outer.get('song'), str) else {}


def wait_track(ws, path, budget):
    """The next a202 naming the track (stock also sends state-only deltas)."""
    deadline = time.monotonic() + budget
    while True:
        event, _ = wait_event(ws, 'a202', deadline)
        now = song(event.encode())
        if now.get('song_file_path') == path:
            return now


def main():
    status, body, _ = call('GET', '/api/health')
    health = json.loads(body)
    assert status == 200 and not health['controlActive'], 'Disconnect the browser first'
    assert health['internalLists'] is True and health['externalLists'] is True and health['historyWrites'] == 'ok', health
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir()
    for n, title in enumerate(TITLES):
        tone(track(n), title, n + 1, 440 + 110 * n, seconds=30)
    total, names = scan()
    note('scanned', {'total': total, 'ours': sorted(n for n in names if 'Evening' in n)})

    # The card in one request: the folder, and the tree with every audio file's facts (MIPS build, stock's card).
    status, raw, _ = call('GET', '/api/card/folder/' + quote(FOLDER.name))
    folder = json.loads(raw)
    assert status == 200 and [e['name'] for e in folder['entries']] == [track(n).name for n in range(3)], (status, raw[:300])
    started = time.monotonic()
    status, raw, headers = call('GET', '/api/card/tree')
    tree = json.loads(raw)
    seconds = round(time.monotonic() - started, 2)
    ours = [e for e in tree['entries'] if e['path'].startswith(FOLDER.name + '/')]
    assert status == 200 and not tree['truncated'] and len(ours) == 3, (status, tree.get('truncated'), ours)
    assert all(e['format'] == 'flac' and e['sampleRate'] == 22050 and e['durationMs'] == 30000 for e in ours), ours
    assert not any(e['path'].startswith('.') for e in tree['entries'])
    note('card_tree', {'files': tree['files'], 'folders': tree['folders'], 'bytes': tree['bytes'], 'json_bytes': len(raw),
                       'seconds': seconds, 'service_ms': tree['elapsedMs']})

    # Written, read back, stored relative to the card root under a UTF-8 BOM and #EXTM3U.
    entries = [str(track(n)) for n in range(3)]
    status, doc = lists('PUT', NAME, {'entries': entries})
    assert status == 201 and doc['entries'] == 3, (status, doc)
    stored = (LISTS/f'{NAME}.m3u').read_text()
    assert stored == '\ufeff#EXTM3U\n' + ''.join(f'Lists Acceptance/{track(n).name}\n' for n in range(3)), stored
    status, doc = lists('GET', NAME)
    assert status == 200 and doc['entries'] == entries, doc
    status, refused = lists('PUT', NAME, {'entries': [entries[0], str(FOLDER/'Missing.flac')]})
    assert status == 400 and refused['entry'] == 1, (status, refused)
    assert (LISTS/f'{NAME}.m3u').read_text() == stored
    note('written', {'path': doc['path'], 'bytes': doc['bytes'], 'refused_entry': refused})

    # Played through the control channel: the card catalog admits folder play with the list's path.
    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    assert ws.status == 101
    ws.send('0599000C0000')
    ws.recv()
    ws.send('token:' + SERIAL)
    path = str(LISTS/f'{NAME}.m3u')

    def request():
        counter[0] += 1
        ws.send(f'request:lists-acceptance-ws-{int(time.time())}-{counter[0]:03d}')
    request(); ws.send(frame('0101', '0004' + path).decode())
    first = wait_track(ws, entries[0], 20)
    # Stock ignores a selection within about 2.1 s of the previous one (its navigation gate,
    # snowsky-disc-qemu docs/protocol): the page waits as long before it selects again.
    time.sleep(2.5)
    # The second entry, sought to 5 s at once: the switch the page uses for "play next".
    request(); ws.send(frame('0100', '0001' + '0004' + path).decode())
    request(); ws.send(frame('0103', f'{5000:08X}').decode())
    second = wait_track(ws, entries[1], 15)
    # The seek followed the selection: stock's ticks run from 5 s, not from the track's start.
    tick, _ = wait_event(ws, 'a103', time.monotonic() + 5)
    tick_ms = int(tick[8:16], 16) if len(tick) >= 16 else None
    time.sleep(1.5)
    ws.send('02020008')
    observed, _ = wait_event(ws, 'a202', time.monotonic() + 5)
    position = json.loads(observed[8:]).get('position') or json.loads(observed[8:]).get('pos')
    request(); ws.send('0201000C0000')
    wait_event(ws, 'a202', time.monotonic() + 10, state=1)
    ws.close()
    assert first.get('song_file_path') == entries[0] and first.get('is_m3u') is True, first
    assert second.get('song_file_path') == entries[1] and second.get('is_m3u') is True, second
    note('played', {'first': first.get('song_file_path'), 'second': second.get('song_file_path'),
                    'm3u_file_path': second.get('m3u_file_path'), 'state_after': json.loads(observed[8:]).get('state'),
                    'position_field': position, 'first_tick_ms': tick_ms})
    assert tick_ms is not None and tick_ms >= 5000, tick_ms

    # Replaced whole, then deleted.
    status, doc = lists('PUT', NAME, {'entries': entries[1:]})
    assert status == 200 and doc['replaced'] is True and doc['entries'] == 2, (status, doc)
    status, doc = lists('DELETE', NAME)
    assert status == 200 and doc == {'name': NAME, 'deleted': True}, (status, doc)
    assert lists('GET', NAME)[0] == 404
    assert not any(p.name.startswith('.list-') for p in LISTS.iterdir())
    note('replaced_and_deleted', {'index': lists('GET')[1]['count']})

    # An external list: in Playlists/ at the card root, where the player's own file browser shows it.
    status, doc = lists('PUT', 'Самое слушаемое', {'entries': entries}, scope='external')
    assert status == 201 and doc['path'] == str(CARD/'Playlists'/'Самое слушаемое.m3u'), (status, doc)
    status, raw, _ = call('GET', '/api/stock/localdir/tmp/sdcard/Playlists/', {'start-pos': '0', 'num-max': '50'})
    rows = json.loads(raw) if status == 200 and raw else []
    assert any(row.get('name') == 'Самое слушаемое.m3u' and row.get('is_m3u') for row in rows), rows
    assert lists('DELETE', 'Самое слушаемое', scope='external')[0] == 200
    shutil.rmtree(CARD/'Playlists', ignore_errors=True)
    note('external_list', {'listed_by_stock': True})

    # A play a page sounded itself: recorded with the service's clock and served with its source.
    before = len(json.loads(call('GET', '/api/history')[1])['records'])
    play = {'path': entries[2], 'seconds': 20, 'ctx': {'type': 4, 'count': 3, 'hash': None, 'album': None,
                                                       'artist': None, 'genre': None, 'folder': str(FOLDER)}}
    data = json.dumps(play).encode()
    status, raw, _ = call('POST', '/api/history', guarded({'Content-Length': str(len(data))}), data)
    added = json.loads(raw)
    assert status == 201 and added['source'] == 'browser', (status, raw)
    records = json.loads(call('GET', '/api/history')[1])['records']
    last = records[-1]
    assert len(records) == before + 1 and last['path'] == entries[2] and last['source'] == 'browser', last
    assert abs(last['t'] + 20 - time.time()) < 60, last['t']
    health = json.loads(call('GET', '/api/health')[1])
    writes = json.loads(call('GET', '/api/about')[1])['database']['writes']
    assert health['historyWrites'] == 'ok' and writes['failed'] == 0, (health, writes)
    note('browser_play', {'record': last, 'writes': writes})

    shutil.rmtree(FOLDER, ignore_errors=True)
    total, _ = scan()
    note('cleaned', {'total': total})
    evidence['status'] = 'passed'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
