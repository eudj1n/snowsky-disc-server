"""Stage B acceptance inside the disposable guest: catalog mutations against stock.

Runs in the emulator container against the guest gateway. Uploads a copy of a
generated track through the gateway, starts a stock scan and waits for its end,
checks the catalog grew, drives playback with guarded mutations and proves a
replayed request ID is refused. Disposable media only; never a physical player.
"""
import http.client
import json
from pathlib import Path
import struct
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/conformance')
from test_service import WS, record  # noqa: E402

from guest_checks import AUTHORITY, CARD, PORT  # noqa: E402
OUT = Path('/work/disc-gateway.json')
summary = {'steps': []}


def step(name, **fields):
    summary['steps'].append({'name': name, **fields}); print(name, json.dumps(fields, ensure_ascii=False))


def call(method, path, headers=None, body=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=30)
    c.request(method, path, body=body, headers={'Host': AUTHORITY, **(headers or {})}); r = c.getresponse()
    out = (r.status, r.read(), {k.lower(): v for k, v in r.getheaders()}); c.close(); return out


def catalog_total():
    status, body, headers = call('GET', '/api/stock/song_category_tree/', {'type': 'all/song', 'start-pos': '0', 'num-max': '200'})
    assert status == 200, (status, body[:120])
    rows = json.loads(body)
    return int(headers['total-num']), [row['name'] for row in rows]


def wait_event(ws, tag, deadline, payload=None, state=None):
    """Next record with the tag (and exact payload or playback state); pings are answered by WS."""
    seen = []
    while time.monotonic() < deadline:
        op, data = ws.recv()
        if op == 8:
            raise AssertionError(f'closed {struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None} while waiting for {tag}; seen {seen}')
        text = data.decode('utf-8', 'replace'); seen.append(text[:40])
        if not text.lower().startswith(tag) or (payload is not None and text[8:] != payload):
            continue
        if state is not None:
            try:
                if json.loads(text[8:]).get('state') != state:
                    continue  # state 2 is loading; wait for the settled state
            except ValueError:
                continue
        return text, seen
    raise AssertionError(f'no {tag} within budget; seen {seen}')


def main():
    # combined-008: the player's serial number is the credential (the guest's is all zero).
    token = '00000000000000'
    counter = [0]; run = int(time.time()); issued = []
    def request(ws):
        counter[0] += 1; issued.append(f'gateway-acceptance-{run}-{counter[0]:03d}'); ws.send('request:' + issued[-1])
    status, body, _ = call('GET', '/api/health'); health = json.loads(body)
    assert status == 200 and health['api'] == 1 and health['readOnly'] is False, health
    step('health', api=health['api'], readOnly=health['readOnly'])
    # The page's policy names the reviewed external origins of the published release, nothing else.
    sys.path.insert(0, '/platform')
    from scripts import origins_catalog
    status, _, headers = call('GET', '/')
    expected = origins_catalog.policy(origins_catalog.load_origins())
    assert status == 200 and headers['content-security-policy'] == expected, headers.get('content-security-policy')
    step('page_policy', origins=len(origins_catalog.load_origins()['origins']))

    status, body, _ = call('GET', '/api/data/system_settings'); assert status == 200, (status, body[:120])
    settings = json.loads(body); row = dict(zip(settings['columns'], settings['rows'][0]))
    languages = ['zh-Hans', 'zh-Hant', 'en', 'ja', 'ko', 'es', 'it', 'de', 'fr', 'ru']
    step('data_system_settings', language_index=row['LANGUAGE'], language=languages[row['LANGUAGE']] if 0 <= row['LANGUAGE'] < 10 else None,
         battery=row['BATTERY'], power_save=row['POWER_SAVE'], max_vol=row['MAX_VOL'])
    status, body, _ = call('GET', '/api/data/library_summary'); assert status == 200, (status, body[:120])
    library = dict(zip(json.loads(body)['columns'], json.loads(body)['rows'][0]))
    status, body, _ = call('GET', '/api/data/tracks?limit=5&offset=0'); assert status == 200, (status, body[:120])
    tracks = json.loads(body)
    assert tracks['rows_returned'] == min(5, library['tracks']), (tracks['rows_returned'], library)
    step('data_library', tracks=library['tracks'], queue_table=library['queue_table'], first_path=dict(zip(tracks['columns'], tracks['rows'][0]))['PATH'] if tracks['rows'] else None)

    source = next(p for p in (CARD/'Кириллица Ё й').iterdir() if p.suffix == '.flac')
    data = source.read_bytes()
    destination = f'/tmp/sdcard/Gateway Upload/Копия {int(time.time())} — {source.name}'
    before_total, _ = catalog_total()
    status, body, _ = call('POST', '/api/stock/audio' + quote(destination),
                           {'X-Disc-Token': token, 'X-Disc-Request': f'gateway-upload-{int(time.time())}-abcdef', 'Content-Length': str(len(data))}, body=data)
    assert status == 201, (status, body[:120])
    assert Path(destination).read_bytes() == data
    step('upload', bytes=len(data), path=destination, catalog_total_before=before_total)

    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    assert ws.status == 101
    ws.send('0599000C0000'); assert ws.recv() == (1, record('a599', '0306'))
    ws.send('token:' + token)
    request(ws); ws.send('0622000C0000')
    finished, seen = wait_event(ws, 'a60a', time.monotonic() + 90, '0005')
    counts = [s for s in seen if s.startswith('a622')]
    step('scan', finished=finished, count_events=len(counts), last_count=counts[-1] if counts else None)
    after_total, names = catalog_total()
    assert after_total == before_total + 1, (before_total, after_total)
    assert Path(destination).name in names, names
    step('catalog_after_scan', total=after_total, uploaded_row=Path(destination).name)

    request(ws); ws.send('0101000C0001')
    playing, _ = wait_event(ws, 'a202', time.monotonic() + 20, state=0)
    time.sleep(0.6)
    request(ws); ws.send('0201000C0000')
    paused, _ = wait_event(ws, 'a202', time.monotonic() + 15, state=1)
    ws.send('02020008'); observed, _ = wait_event(ws, 'a202', time.monotonic() + 5)
    step('playback', after_play_all=json.loads(playing[8:]).get('state'), after_toggle=json.loads(paused[8:]).get('state'),
         read_state=json.loads(observed[8:]).get('state'))
    ws.send('0599000C0000'); wait_event(ws, 'a599', time.monotonic() + 5, '0306')  # session still healthy
    ws.close(); time.sleep(0.5)

    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    ws.send('0599000C0000'); wait_event(ws, 'a599', time.monotonic() + 5, '0306')
    ws.send('token:' + token); ws.send('request:' + issued[0]); ws.send('0201000C0000')  # exact replay of a used ID
    while True:
        op, data = ws.recv()
        if op == 8:
            break
    assert data == struct.pack('!H', 1008), data
    step('replay_refused', close=1008)
    ws.close()
    summary['status'] = 'passed'
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001
        summary['status'] = f'failed: {error!r}'
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        raise
