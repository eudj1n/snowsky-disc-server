"""Combined-008 acceptance inside the disposable guest: the play history in the card database.

`record`: plays the generated album on stock through the gateway until the observer records a play
in /tmp/sdcard/.disc/disc.db, checks the file (schema version, rollback journal, the row the route
serves) and samples that the service holds nothing open on the card, then pauses. `reopen`, after
the host restarted the service: the database is checked again on its first open and the route serves
the same records. Disposable media only; never a physical player.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, '/platform')
sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from test_service import WS  # noqa: E402
from gateway_mutation import AUTHORITY, PORT, call, wait_event  # noqa: E402
from guest_checks import guest_processes  # noqa: E402
from scripts import disc_database  # noqa: E402

DATABASE = Path('/tmp/sdcard/.disc/disc.db')
OUT = Path('/work/disc-history.json')
SERIAL = '00000000000000'  # the guest's all-zero SN, the only credential since combined-008


def history():
    status, body, _ = call('GET', '/api/history')
    assert status == 200, (status, body[:120])
    return json.loads(body)['records']


def service_card_files():
    """Files on the card the service's process holds open right now."""
    held = []
    for pid in guest_processes('disc-service'):
        try:
            for fd in os.listdir(f'/proc/{pid}/fd'):
                target = os.readlink(f'/proc/{pid}/fd/{fd}')
                if '/tmp/sdcard/' in target:
                    held.append(target)
        except OSError:
            continue
    return held


def control(request, command, state):
    """Sends one guarded playback command and waits for the settled state."""
    ws = WS(PORT, origin=f'http://{AUTHORITY}', host=AUTHORITY)
    try:
        ws.send('0599000C0000'); wait_event(ws, 'a599', time.monotonic() + 5, '0306')
        ws.send('token:' + SERIAL); ws.send('request:' + request); ws.send(command)
        wait_event(ws, 'a202', time.monotonic() + 20, state=state)
    finally:
        ws.close()


def record(summary):
    status, body, _ = call('GET', '/api/health')
    assert status == 200 and json.loads(body)['history'] is True, body
    before = history()
    run = int(time.time())
    # One short control session per command: an idle session would miss the service's pings.
    control(f'history-acceptance-{run}-001', '0101000C0001', 0)
    started, records = time.monotonic(), before
    while time.monotonic() - started < 60 and len(records) == len(before):
        time.sleep(1); records = history()
    assert len(records) == len(before) + 1, (len(before), len(records))
    waited = round(time.monotonic() - started, 1)
    control(f'history-acceptance-{run}-002', '0201000C0000', 1)
    newest = records[-1]
    with closing(sqlite3.connect(f'file:{DATABASE}?mode=ro', uri=True)) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        journal = db.execute('PRAGMA journal_mode').fetchone()[0]
        rows = db.execute('SELECT count(*) FROM plays').fetchone()[0]
        last = db.execute('SELECT started_at, path, heard_seconds FROM plays ORDER BY id DESC LIMIT 1').fetchone()
    assert (version, journal) == (disc_database.schema_sql()[1], 'delete'), (version, journal)
    assert last == (newest['t'], newest['path'], newest['s']), (last, newest)
    assert not DATABASE.with_name('disc.db-journal').exists()
    samples = [service_card_files() for _ in range(40) if not time.sleep(.1)]
    assert not any(samples), samples
    summary['record'] = {'waited_s': waited, 'rows': rows, 'served': len(records), 'path': newest['path'],
                         'heard_s': newest['s'], 'context_type': newest['ctx']['type'], 'user_version': version,
                         'journal_mode': journal, 'fd_samples_without_card_files': len(samples)}
    summary['served'] = records


def reopen(summary):
    records = history()
    assert records == summary['served'], 'the restarted service serves other records'
    summary['reopen'] = {'served': len(records), 'same_records': True, 'card_files_held': service_card_files()}
    assert not summary['reopen']['card_files_held']


def main():
    phase = sys.argv[1]
    summary = json.loads(OUT.read_text()) if phase == 'reopen' else {}
    try:
        {'record': record, 'reopen': reopen}[phase](summary)
        summary['status'] = 'passed' if phase == 'reopen' else 'recorded'
    except Exception as error:  # noqa: BLE001
        summary['status'] = f'failed in {phase}: {error!r}'
        raise
    finally:
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        print(json.dumps({k: v for k, v in summary.items() if k != 'served'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
