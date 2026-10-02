"""Combined-008 acceptance inside the disposable guest: disliked tracks end to end on stock.

The reviewed store catalog on the card declares the "disliked" collection; nothing in the image
knows it by name. The script dislikes the second generated track through the store with the
guest's SN, reads it back through a reviewed query over the card database, starts "play all" with
a short control session and closes it (no page holds control), seeks near the end of the first
track and watches stock's open file: the disliked track starts, the service skips it with stock's
own "next" and the third track plays; the skipped track is never recorded as a play. The dislike
is removed and playback paused at the end. Disposable media only; never a physical player.
"""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from test_service import WS  # noqa: E402
from gateway_mutation import AUTHORITY, PORT, call, wait_event  # noqa: E402

ALBUM = Path('/tmp/sdcard/Кириллица Ё й')
DISLIKED = ALBUM/'Second — Ё.flac'
FOLLOWING = ALBUM/'Third — й.flac'
LOG = Path('/work/disc-service.log')
OUT = Path('/work/disc-store.json')
SERIAL = '00000000000000'
summary = {'steps': []}


def step(name, **fields):
    summary['steps'].append({'name': name, **fields}); print(name, json.dumps(fields, ensure_ascii=False), flush=True)


def guarded(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {'X-Disc-Token': SERIAL, 'X-Disc-Request': f'store-acceptance-{time.time_ns()}'}
    if data is not None:
        headers['Content-Length'] = str(len(data))
    return call(method, path, headers, data)


def control(*records):
    """One short guarded control session: each record waits for its reply, then the session closes."""
    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    try:
        ws.send('0599000C0000'); wait_event(ws, 'a599', time.monotonic() + 5, '0306')
        ws.send('token:' + SERIAL)
        for record, reply, state in records:
            ws.send(f'request:store-acceptance-{time.time_ns()}'); ws.send(record)
            wait_event(ws, reply, time.monotonic() + 20, state=state)
    finally:
        ws.close()


def player_file():
    """The music file mq_player holds open, or None."""
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            if b'/usr/bin/mq_player' not in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
                continue
            for fd in sorted(os.listdir(f'/proc/{pid}/fd'), key=int):
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
                if '/tmp/sdcard/' in target and target.lower().endswith(('.flac', '.wav', '.mp3')):
                    return target.split('/tmp/sdcard/', 1)[1]
        except OSError:
            continue
    return None


def main():
    status, body, _ = call('GET', '/api/health'); health = json.loads(body)
    assert status == 200 and health.get('store') is True, health
    status, body, _ = call('GET', '/api/store'); assert status == 200, (status, body[:120])
    assert json.loads(body)['collections']['disliked']['skip'] is True
    status, body, _ = guarded('PUT', '/api/store/disliked/record', {'path': str(DISLIKED), 'artist': 'CI Artist', 'at': int(time.time())})
    assert status == 200, (status, body[:160])
    step('disliked', key=json.loads(body)['key'], created=json.loads(body)['created'])
    status, body, _ = call('GET', '/api/data/disliked_tracks?limit=10&offset=0'); rows = json.loads(body)['rows']
    assert status == 200 and rows and rows[0][0] == str(DISLIKED), (status, body[:200])
    step('query', rows=len(rows), plays=rows[0][3])
    status, body, _ = call('GET', '/api/history'); plays_before = [r['path'] for r in json.loads(body)['records']]
    log_mark = LOG.stat().st_size
    # Play all, then 26 s into the first track; the session closes, so the service applies the rule.
    control(('0101000C0001', 'a202', 0), ('0103001000006590', 'a103', None))
    first = player_file()
    started, seen = time.monotonic(), []
    disliked_at = following_at = None
    while time.monotonic() - started < 60 and following_at is None:
        current = player_file()
        if not seen or seen[-1] != current: seen.append(current)
        if current == str(DISLIKED.relative_to('/tmp/sdcard')) and disliked_at is None: disliked_at = time.monotonic()
        if current == str(FOLLOWING.relative_to('/tmp/sdcard')) and disliked_at is not None: following_at = time.monotonic()
        time.sleep(.2)
    assert disliked_at and following_at, (first, seen)
    held = round(following_at - disliked_at, 1)
    assert held < 15, held  # the file is 30 s long; skipped, not played through
    # The service reports once stock confirmed the following track (or its budget ran out).
    deadline, log = time.monotonic() + 12, ''
    while time.monotonic() < deadline and 'Skip rule:' not in log:
        time.sleep(.5); log = LOG.read_bytes()[log_mark:].decode(errors='replace')
    assert 'Skip rule: skipped to the next track' in log, log[-400:]
    step('skipped', first=first, sequence=seen, disliked_held_s=held, log=[line for line in log.splitlines() if 'Skip rule' in line])
    time.sleep(1)
    status, body, _ = call('GET', '/api/history')
    new = [r['path'] for r in json.loads(body)['records']][len(plays_before):]
    assert str(DISLIKED) not in new, new
    step('history', new_records=len(new), disliked_recorded=False)
    status, body, _ = guarded('DELETE', '/api/store/disliked/record?path=' + quote(str(DISLIKED)))
    assert status == 200 and json.loads(body)['deleted'] is True, (status, body[:120])
    control(('0201000C0000', 'a202', 1))
    step('cleanup', undisliked=True, paused=True)
    summary['status'] = 'passed'


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001
        summary['status'] = f'failed: {error!r}'
        raise
    finally:
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
