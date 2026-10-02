"""Combined-008 acceptance inside the disposable guest: the trash on stock's card.

A probe folder (a copy of a generated track and a cover) is created on the disposable card and
scanned into stock's library. Moved to the trash through the gateway with the guest's SN, its
files wait under .disc/trash with the ".trashed" suffix, and a new scan drops them from the
library although stock's scanner indexes audio inside .disc: the suffix is what keeps it out.
Restored, the folder is back and scanned in again; a cover upload with X-Disc-Replace sends the
old cover to the trash; the file stock holds open is refused. Emptying the trash deletes for good
and the library ends as it began. Disposable media only; never a physical player.
"""
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from test_service import WS  # noqa: E402
from gateway_mutation import AUTHORITY, PORT, call, catalog_total, wait_event  # noqa: E402
from history_database import SERIAL  # noqa: E402

CARD = Path('/tmp/sdcard')
PROBE = CARD/'Trash Probe'
TRACK = PROBE/'Probe — Ё.flac'
BIN = CARD/'.disc'/'trash'
OUT = Path('/work/disc-trash.json')
summary = {'steps': []}


def step(name, **fields):
    summary['steps'].append({'name': name, **fields}); print(name, json.dumps(fields, ensure_ascii=False), flush=True)


def guarded(method, path, body=None, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    h = {'X-Disc-Token': SERIAL, 'X-Disc-Request': f'trash-acceptance-{time.time_ns()}', **(headers or {})}
    if data is not None:
        h['Content-Length'] = str(len(data))
    status, raw, _ = call(method, path, h, data)
    return status, json.loads(raw) if raw.startswith(b'{') else raw


def scan():
    """One guarded library scan to its end; returns the catalog's names."""
    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    try:
        ws.send('0599000C0000'); wait_event(ws, 'a599', time.monotonic() + 5, '0306')
        ws.send('token:' + SERIAL); ws.send(f'request:trash-acceptance-{time.time_ns()}'); ws.send('0622000C0000')
        wait_event(ws, 'a60a', time.monotonic() + 90, '0005')
    finally:
        ws.close()
    time.sleep(.5)
    return catalog_total()


def trashed_files():
    return sorted(str(p.relative_to(BIN)) for p in BIN.rglob('*') if p.is_file()) if BIN.is_dir() else []


def main():
    status, body, _ = call('GET', '/api/health'); health = json.loads(body)
    assert status == 200 and health.get('trash') is True, health
    # Start from an empty trash: the page's emulator acceptance leaves entries of its own.
    status, doc = guarded('DELETE', '/api/trash')
    assert status == 200 and trashed_files() == [], (status, doc, trashed_files())
    start_total, _ = catalog_total()
    shutil.rmtree(PROBE, ignore_errors=True); PROBE.mkdir()
    shutil.copyfile(CARD/'Кириллица Ё й'/'Second — Ё.flac', TRACK)
    (PROBE/'cover.jpg').write_bytes(b'\xff\xd8\xff\xe0old cover\xff\xd9')
    total, names = scan()
    assert total == start_total + 1 and TRACK.name in names, (start_total, total, names)
    step('probe_scanned', total=total)

    status, doc = guarded('POST', '/api/trash', {'path': str(PROBE)})
    assert status == 200 and doc['kind'] == 'folder' and doc['files'] == 2, (status, doc)
    entry = doc['id']
    files = trashed_files()
    assert not PROBE.exists() and all(name.endswith('.trashed') for name in files), files
    total, names = scan()
    assert total == start_total and TRACK.name not in names, (total, names)
    step('trashed_and_rescanned', id=entry, files=files, total=total, probe_indexed=False)

    status, doc = guarded('POST', f'/api/trash/{entry}/restore')
    assert status == 200 and doc['restored'] is True, (status, doc)
    total, names = scan()
    assert TRACK.is_file() and total == start_total + 1 and TRACK.name in names, (total, names)
    step('restored_and_rescanned', total=total)

    new_cover = b'\xff\xd8\xff\xe0new cover\xff\xd9'
    status, doc = guarded('POST', '/api/stock/audio' + quote(str(PROBE/'cover.jpg')), new_cover, {'X-Disc-Replace': 'trash'})
    assert status == 201 and doc['replaced'] is True and (PROBE/'cover.jpg').read_bytes() == new_cover, (status, doc)
    status, listing = guarded('GET', '/api/trash')
    assert [e['path'] for e in listing['entries']] == [str(PROBE/'cover.jpg')], listing
    step('cover_replaced', trash_entries=listing['count'])

    status, body, _ = call('GET', '/api/history')
    held = None
    for pid_file in ('Кириллица Ё й/Second — Ё.flac', 'Кириллица Ё й/Third — й.flac', 'Кириллица Ё й/CI Tone — Проверка.wav'):
        status, doc = guarded('POST', '/api/trash', {'path': str(CARD/pid_file)})
        if status == 409:
            held = pid_file; break
        assert status == 200, (status, doc)
        guarded('POST', f'/api/trash/{doc["id"]}/restore')
    assert held, 'stock holds no generated track open'
    step('held_refused', status=409)

    # macOS leftovers: AppleDouble files and the volume's Finder trash go as one entry; the library is unchanged.
    (PROBE/'._cover.jpg').write_bytes(b'\x00\x05\x16\x07' + b'\0' * 4092)
    (CARD/'.Trashes'/'501').mkdir(parents=True, exist_ok=True)
    (CARD/'.Trashes'/'501'/'Deleted — Ё.flac').write_bytes((CARD/'Кириллица Ё й'/'Third — й.flac').read_bytes())
    status, report = guarded('GET', '/api/card/leftovers')
    assert status == 200 and report['files'] >= 2, report
    status, doc = guarded('POST', '/api/card/leftovers/trash')
    assert status == 200 and doc['kind'] == 'leftovers' and doc['files'] == report['files'], (status, doc)
    assert not (CARD/'.Trashes').exists() and not (PROBE/'._cover.jpg').exists()
    total, names = scan()
    assert total == start_total + 1, (total, names)
    step('leftovers_trashed', files=doc['files'], bytes=doc['bytes'], library_total=total)

    status, doc = guarded('POST', '/api/trash', {'path': str(PROBE)})
    assert status == 200, (status, doc)
    status, doc = guarded('DELETE', '/api/trash')
    assert status == 200 and doc['purged'] == 3 and trashed_files() == [], (status, doc, trashed_files())
    total, names = scan()
    assert total == start_total and not PROBE.exists(), total
    step('emptied', purged=doc['purged'], total=total)
    summary['status'] = 'passed'


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001
        summary['status'] = f'failed: {error!r}'
        raise
    finally:
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        shutil.rmtree(PROBE, ignore_errors=True)
