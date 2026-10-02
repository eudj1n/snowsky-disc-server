"""Research inside the disposable guest: how stock V2.57 treats M3U lists (docs/m3u.md).

Generated media only, never a player. Tagged FLAC tones and a small three-track CUE image are
scanned, one more file is left unscanned, and lists of several forms are written to the service's
hidden .disc/playlists. Each list is played through folder play (0101, list type 0004, the list's
path), and stock's now-playing record and queue table are read back. Then: a position, next and
previous, a missing entry, a list changed while it plays, the service's history record of a list
play, a favorite set from a list and the same file played from its folder, stock's batch command
0111 over the link, and whether the browsers list the hidden folder. Everything generated is
removed and the library rescanned; the caller prepares the guest again. Evidence goes to
/work/disc-m3u.json.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from controller.fiio_link import Frames, frame  # noqa: E402
from gateway_mutation import AUTHORITY, call  # noqa: E402
from trash_card import scan  # noqa: E402

CARD = Path('/tmp/sdcard')
ALBUM = CARD/'M3U Research'
IMAGE_DIR = CARD/'M3U Research CUE'
LISTS = CARD/'.disc'/'playlists'
EXPORT = CARD/'export_m3u'
SONG_DB = 'file:/work/rootfs/usr/data/fiio/db/song.db?mode=ro'
OUT = Path('/work/disc-m3u.json')
SERIAL = '00000000000000'
TITLES = ('Alpha', 'Bravo', 'Charlie', 'Delta')
CUE_TITLES = ('Image One', 'Image Two', 'Image Three')
evidence = {'variants': {}, 'steps': {}}


def note(name, value):
    evidence['steps'][name] = value
    print(name, json.dumps(value, ensure_ascii=False)[:600], flush=True)


def tone(path, title, number, frequency, seconds=25):
    subprocess.run(['sox', '-n', '--comment', f'TITLE={title}', '--add-comment', f'TRACKNUMBER={number}',
                    '--add-comment', 'ALBUM=M3U Research', '--add-comment', 'ARTIST=M3U Artist',
                    '-r', '22050', '-c', '1', '-b', '16', str(path), 'synth', str(seconds), 'sine', str(frequency), 'vol', '0.1'],
                   check=True)


def make_media():
    for folder in (ALBUM, IMAGE_DIR, LISTS, EXPORT):
        shutil.rmtree(folder, ignore_errors=True)
    ALBUM.mkdir(); IMAGE_DIR.mkdir(); LISTS.mkdir(parents=True)
    for n, title in enumerate(TITLES):
        tone(ALBUM/f'{n + 1} {title}.flac', title, n + 1, 330 + 110 * n)
    parts = []
    for n in range(3):  # small: the guest's card holds about 38 MB
        part = IMAGE_DIR/f'{n}.wav'
        subprocess.run(['sox', '-n', '-r', '22050', '-b', '16', '-c', '1', str(part), 'synth', '10', 'sine', str(500 + 100 * n), 'vol', '0.1'], check=True)
        parts.append(str(part))
    subprocess.run(['sox', *parts, str(IMAGE_DIR/'Image.flac')], check=True)
    for part in parts:
        Path(part).unlink()
    sheet = ['PERFORMER "M3U Artist"', 'TITLE "M3U Research CUE"', 'FILE "Image.flac" WAVE']
    for n, title in enumerate(CUE_TITLES):
        sheet += [f'  TRACK {n + 1:02} AUDIO', f'    TITLE "{title}"', f'    INDEX 01 00:{10 * n:02}:00']
    (IMAGE_DIR/'Image.cue').write_text('\r\n'.join(sheet) + '\r\n')


def rel(path):
    return str(Path(path).relative_to(CARD))


def track(n):
    return ALBUM/f'{n + 1} {TITLES[n]}.flac'


def write_lists():
    a, b, c, d = (rel(track(n)) for n in range(4))
    unscanned = ALBUM/'5 Echo.flac'
    tone(unscanned, 'Echo', 5, 770)
    forms = {
        'base.m3u': ('#EXTM3U\n' + '\n'.join((a, b, c)) + '\n').encode(),
        'bare.m3u': ('\n'.join('/' + x for x in (a, b)) + '\n').encode(),
        'crlf.m3u': ('#EXTM3U\r\n' + '\r\n'.join((a, b)) + '\r\n').encode(),
        'bom.m3u': ('﻿#EXTM3U\n' + '\n'.join((a, b)) + '\n').encode('utf-8'),
        'extinf.m3u': (f'#EXTM3U\n#EXTINF:25,M3U Artist - Something Else\n{a}\n#EXTINF:25,M3U Artist - Another\n{b}\n').encode(),
        'list.m3u8': ('#EXTM3U\n' + '\n'.join((b, a)) + '\n').encode(),
        'missing.m3u': ('#EXTM3U\n' + '\n'.join((a, 'M3U Research/9 Nowhere.flac', b)) + '\n').encode(),
        'unscanned.m3u': ('#EXTM3U\n' + '\n'.join((rel(unscanned), a)) + '\n').encode(),
        'absolute.m3u': ('#EXTM3U\n' + '\n'.join(str(track(n)) for n in (0, 1)) + '\n').encode(),
        'cue_image.m3u': ('#EXTM3U\n' + rel(IMAGE_DIR/'Image.flac') + '\n').encode(),
        'cue_sheet.m3u': ('#EXTM3U\n' + rel(IMAGE_DIR/'Image.cue') + '\n').encode(),
        'long.m3u': ('#EXTM3U\n' + '\n'.join((a, b, c, d)[n % 4] for n in range(600)) + '\n').encode(),
    }
    for name, data in forms.items():
        (LISTS/name).write_bytes(data)
    return forms


def stock(*records, collect=3.0):
    """One direct stock session: handshake, then each (tag, payload) and what arrives within `collect` s."""
    deadline = time.monotonic() + 20
    while True:
        try:
            with socket.create_connection(('127.0.0.1', 12100), timeout=5) as peer:
                parser, seen = Frames(), []

                def read(until_tag, budget):
                    end = time.monotonic() + budget
                    while time.monotonic() < end:
                        peer.settimeout(max(0.05, end - time.monotonic()))
                        try:
                            data = peer.recv(65536)
                        except socket.timeout:
                            return
                        if not data:
                            return
                        for tag, payload in parser.feed(data):
                            seen.append((tag, payload))
                            if tag == until_tag:
                                return
                peer.sendall(frame('0599', '0000')); read('a599', 5)
                seen.clear()
                for tag, payload in records:
                    peer.sendall(frame(tag, payload))
                    read('____', collect)
                return seen
        except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError):
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def song_of(payload):
    try:
        outer = json.loads(payload.decode())
        song = outer.get('song')
        return json.loads(song) if isinstance(song, str) else None
    except ValueError:
        return None


def current():
    """The now-playing record's fields that matter here, and stock's state."""
    records = stock(('0202', ''), collect=2.0)
    song, state = None, None
    for tag, payload in records:
        if tag != 'a202':
            continue
        try:
            state = json.loads(payload.decode()).get('state', state)
        except ValueError:
            pass
        song = song_of(payload) or song
    keys = ('id', 'pos_id', 'song_name', 'song_file_path', 'song_duration_time', 'song_sample_rate', 'is_m3u', 'm3u_file_path')
    love = None
    for tag, payload in records:
        if tag == 'a202':
            try:
                love = json.loads(payload.decode()).get('love', love)
            except ValueError:
                pass
    return {'state': state, 'love': love, **({k: song.get(k) for k in keys} if song else {})}


def queue():
    db = sqlite3.connect(SONG_DB, uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'LIST_SONG_0'").fetchone():
            return {'table': False}
        rows = db.execute('SELECT PATH, SONG_TYPE, IS_M3U, M3U_PATH, IS_CUE, TITLE FROM LIST_SONG_0 ORDER BY ID').fetchall()
        return {'table': True, 'count': len(rows), 'first': [list(r) for r in rows[:4]]}
    finally:
        db.close()


def play(list_name, position=None):
    path = str(LISTS/list_name)
    if position is None:
        stock(('0101', '0004' + path), collect=3.0)
    else:
        stock(('0100', f'{position:04X}0004' + path), collect=3.0)
    time.sleep(1.5)
    return current()


def pause():
    now = current()
    if now.get('state') == 0:  # wire state 0 playing, 1 paused (the first run toggled paused lists instead)
        stock(('0201', '0000'), collect=1.5)


def fnv_sum(paths):
    total = 0
    for path in paths:
        h = 1469598103934665603
        for byte in path.encode():
            h = ((h ^ byte) * 1099511628211) & 0xffffffffffffffff
        total = (total + h) & 0xffffffffffffffff
    return f'{total:016x}'


def history():
    status, body, _ = call('GET', '/api/history')
    assert status == 200, status
    return json.loads(body)['records']


def loves():
    db = sqlite3.connect(SONG_DB, uri=True)
    try:
        return [list(r) for r in db.execute(
            "SELECT PATH, IS_M3U, M3U_PATH FROM MY_LOVE WHERE PATH LIKE '/tmp/sdcard/M3U Research%' ORDER BY ID")]
    finally:
        db.close()


def clear_loves():
    """Removes favorites under the research folders through the gateway, like the page (SN, request ID)."""
    for _ in range(20):
        status, body, _ = call('GET', '/api/stock/song_category_tree/', {'type': 'love/song', 'start-pos': '0', 'num-max': '200'})
        rows = json.loads(body) if status == 200 and body else []
        ours = [row.get('pos', i) for i, row in enumerate(rows)
                if any(t in str(row.get('name', '')) for t in TITLES + ('Echo', 'Image'))]
        if not ours:
            return
        data = json.dumps([[ours[0], ours[0]]]).encode()
        call('DELETE', '/api/stock/song_category_tree/', {
            'type': 'love/song', 'delete_source': '0', 'X-Disc-Token': SERIAL,
            'X-Disc-Request': f'm3u-research-{time.time_ns()}', 'Content-Length': str(len(data))}, data)
        time.sleep(2.2)


def main():
    status, body, _ = call('GET', '/api/health')
    assert status == 200 and not json.loads(body)['controlActive'], 'Disconnect the browser first'
    make_media()
    total, names = scan()
    note('scanned', {'total': total, 'research_files_indexed': sorted(n for n in names if any(t in n for t in TITLES))})
    forms = write_lists()

    # 1. Each form, played by path.
    for name in forms:
        played = play(name)
        evidence['variants'][name] = {'now': played, 'queue': queue()}
        print('variant', name, json.dumps(evidence['variants'][name], ensure_ascii=False)[:400], flush=True)
        pause()

    # 2. A position, and navigation inside the list.
    note('start_at_position_2', play('base.m3u', 2))
    stock(('0201', '0001'), collect=2.0); time.sleep(1)
    note('next', current())
    stock(('0201', '0002'), collect=2.0); time.sleep(1)
    note('previous', current())
    pause()

    # 3. The list changes while it plays: does next follow the file or the queue stock built?
    play('base.m3u')
    (LISTS/'base.m3u').write_text('#EXTM3U\n' + '\n'.join(rel(track(n)) for n in (3, 2, 1)) + '\n')
    stock(('0201', '0001'), collect=2.0); time.sleep(1)
    note('changed_while_playing_next', current())
    pause()

    # 4. The service's play history of a list play.
    (LISTS/'base.m3u').write_text('#EXTM3U\n' + '\n'.join(rel(track(n)) for n in (0, 1, 2)) + '\n')
    count = len(history())
    play('base.m3u')
    time.sleep(16)
    records = history()[count:]
    rows = queue()
    note('history', {'records': [{'path': r['path'], 'ctx': r['ctx']} for r in records],
                     'hash_of_list_paths': fnv_sum([str(track(n)) for n in (0, 1, 2)]), 'queue': rows})

    # 5. A missing entry: next from the entry before it.
    play('missing.m3u')
    stock(('0201', '0001'), collect=3.0); time.sleep(1.5)
    note('missing_entry_next', current())
    pause()

    # 6. A favorite set from a list, then the same file played from its folder, then taken back.
    play('base.m3u')
    stock(('0104', '0001'), collect=2.0)
    love = loves()
    from_list = current()
    stock(('0101', '0004' + str(ALBUM)), collect=3.0); time.sleep(1.5)
    from_folder = current()
    play('base.m3u')
    stock(('0104', '0000'), collect=2.0)
    note('favorite_from_list', {'rows_after_set': love, 'now_from_list': from_list,
                                'same_file_from_folder': from_folder, 'rows_after_unset': loves()})
    pause()

    # 7a. Stock's batch command 0111 (import M3U) over the link.
    mark = Path('/work/mq_player.log').stat().st_size
    stock(('0111', json.dumps({'path': str(LISTS)})), collect=3.0)
    log = Path('/work/mq_player.log').read_bytes()[mark:].decode('utf-8', 'replace')
    note('batch_import_over_link', {'refused': 'cmd invalid, cmd=0111' in log,
                                    'lines': [line[-120:] for line in log.splitlines() if '0111' in line][:2]})

    # 7b. What the browsers list at the card root.
    status, body, _ = call('GET', '/api/stock/localdir/tmp/sdcard/', {'start-pos': '0', 'num-max': '200'})
    local = [row['name'] for row in json.loads(body)] if status == 200 and body else None
    status, body, _ = call('GET', '/api/stock/dir/tmp/sdcard/', {'start-pos': '0', 'num-max': '200'})
    transfer = [row['name'] for row in json.loads(body)] if status == 200 and body else None
    note('hidden_folder_listed', {'localdir_has_disc': '.disc' in (local or []), 'dir_has_disc': '.disc' in (transfer or [])})

    pause()
    clear_loves()
    note('favorites_left', loves())
    for folder in (ALBUM, IMAGE_DIR, LISTS, EXPORT):
        shutil.rmtree(folder, ignore_errors=True)
    total, _ = scan()
    note('cleaned', {'total': total})
    evidence['status'] = 'recorded'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
