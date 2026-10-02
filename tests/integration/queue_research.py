"""Research inside the disposable guest for combined-009 (docs/m3u.md): long M3U lists, a list in a
visible folder, what a listener hears when the queue is swapped to a list, a seek right after a
selection, and whether stock keeps its queue over a pause and over an idle power-off.

Generated media only, never a player. Three tagged tracks start with a 3-second 1760 Hz marker and
continue with a tone of their own, so the PCM that stock writes to the emulator's audio capture
(/work/rootfs/audio.pcm) shows what sounded and for how long. Actions:

  run          lists, the visible folder, swaps, a seek right after a selection, a short pause
  before-idle  play the research folder from its second track, seek, pause; record the queue
  wait-off     wait for stock's own idle power-off (five minutes paused)
  after-boot   after the idle power-off and a boot: what stock kept, and 0100 on the current queue
  favorites    a favorite set while a list plays against stock's UNIQUE(PATH, TRACK) and the service's
               favorite_add, and the same file's favorite in the library's context
  cleanup      remove everything generated and rescan

Evidence accumulates in /work/disc-queue-research.json.
"""
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import sys
import time

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from controller.fiio_link import Frames, frame  # noqa: E402
from gateway_mutation import call  # noqa: E402
from m3u_playback import current, queue, stock, tone  # noqa: E402
from trash_card import scan  # noqa: E402

CARD = Path('/tmp/sdcard')
FOLDER = CARD/'Queue Research'
LISTS = CARD/'.disc'/'playlists'
VISIBLE = CARD/'Playlists'
ROOTFS = Path('/work/rootfs')
CAPTURE, FORMAT = ROOTFS/'audio.pcm', ROOTFS/'audio.fmt'
OUT = Path('/work/disc-queue-research.json')
TITLES = ('Swap One', 'Swap Two', 'Swap Three')
BODIES = (440, 550, 660)
MARKER = 1760
evidence = json.loads(OUT.read_text()) if OUT.exists() else {}


def note(name, value):
    evidence[name] = value
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(name, json.dumps(value, ensure_ascii=False)[:700], flush=True)


def track(n):
    return FOLDER/f'{n + 1} {TITLES[n]}.flac'


def rel(path):
    return str(Path(path).relative_to(CARD))


def make_media():
    import subprocess
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir()
    for n, title in enumerate(TITLES):
        head, body = FOLDER/f'{n}-a.wav', FOLDER/f'{n}-b.wav'
        subprocess.run(['sox', '-n', '-r', '22050', '-c', '1', '-b', '16', str(head), 'synth', '3', 'sine', str(MARKER), 'vol', '0.3'], check=True)
        subprocess.run(['sox', '-n', '-r', '22050', '-c', '1', '-b', '16', str(body), 'synth', '27', 'sine', str(BODIES[n]), 'vol', '0.3'], check=True)
        subprocess.run(['sox', str(head), str(body), '--comment', f'TITLE={title}', '--add-comment', f'TRACKNUMBER={n + 1}',
                        '--add-comment', 'ALBUM=Queue Research', '--add-comment', 'ARTIST=Queue Artist', str(track(n))], check=True)
        head.unlink(); body.unlink()


def write_list(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#EXTM3U\n' + '\n'.join(entries) + '\n')


# ---- what sounded ----

def capture_mark():
    """Where the capture stands now: its inode and size (stock replaces the file at each PCM open)."""
    try:
        st = CAPTURE.stat()
        return {'inode': st.st_ino, 'size': st.st_size}
    except FileNotFoundError:
        return {'inode': None, 'size': 0}


def heard(mark, limit_s=6.0):
    """Runs of what the capture holds since `mark`: marker, a track's body tone, or silence, in ms."""
    try:
        channels, width, rate = struct.unpack('<III', FORMAT.read_bytes()[:12])
        st = CAPTURE.stat()
    except (FileNotFoundError, struct.error):
        return {'error': 'no capture'}
    start = mark['size'] if st.st_ino == mark['inode'] else 0
    frame_bytes = channels * width
    start -= start % frame_bytes
    with CAPTURE.open('rb') as f:
        f.seek(start)
        data = f.read(int(limit_s * rate) * frame_bytes)
    window = max(1, rate // 50)  # 20 ms
    runs = []
    for at in range(0, len(data) // frame_bytes - window + 1, window):
        crossings, loud, previous = 0, 0, None
        for i in range(at, at + window):
            offset = i * frame_bytes
            sample = int.from_bytes(data[offset:offset + width], 'little', signed=True)
            if abs(sample) > (1 << (8 * width - 1)) // 100:
                loud += 1
            sign = sample >= 0
            if previous is not None and sign != previous:
                crossings += 1
            previous = sign
        frequency = crossings * rate / (2 * window)
        if loud < window // 4:
            kind = 'silence'
        elif abs(frequency - MARKER) < 150:
            kind = 'marker'
        else:
            kind = min(zip(BODIES, TITLES), key=lambda pair: abs(pair[0] - frequency))[1]
        if runs and runs[-1][0] == kind:
            runs[-1][1] += 20
        else:
            runs.append([kind, 20])
    return {'format': {'channels': channels, 'bytes': width, 'rate': rate}, 'from_start': start == 0, 'runs': runs[:24]}


# ---- stock ----

def session():
    # Stock briefly removes its listener after a session ends: retry the connection only.
    deadline = time.monotonic() + 20
    while True:
        try:
            peer = socket.create_connection(('127.0.0.1', 12100), timeout=5)
            break
        except ConnectionRefusedError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)
    parser = Frames()

    def send(tag, payload):
        peer.sendall(frame(tag, payload))

    def read_until(test, budget):
        end = time.monotonic() + budget
        while time.monotonic() < end:
            peer.settimeout(max(0.02, end - time.monotonic()))
            try:
                data = peer.recv(65536)
            except socket.timeout:
                return None
            if not data:
                return None
            for tag, payload in parser.feed(data):
                if test(tag, payload):
                    return tag, payload
        return None
    send('0599', '0000')
    read_until(lambda tag, _: tag == 'a599', 5)
    return peer, send, read_until


def playing_path(path):
    def test(tag, payload):
        if tag != 'a202':
            return False
        try:
            outer = json.loads(payload.decode())
            song = json.loads(outer['song']) if isinstance(outer.get('song'), str) else None
        except (ValueError, KeyError):
            return False
        return bool(song) and song.get('song_file_path') == path
    return test


def pause():
    if current().get('state') == 0:  # wire state 0 playing, 1 paused, 2 stopped
        stock(('0201', '0000'), collect=1.5)


def queue_hash():
    db = sqlite3.connect('file:/work/rootfs/usr/data/fiio/db/song.db?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'LIST_SONG_0'").fetchone():
            return None
        rows = db.execute('SELECT PATH, IS_M3U FROM LIST_SONG_0 ORDER BY ID').fetchall()
        return {'count': len(rows), 'paths': [r[0][len('/tmp/sdcard/'):] for r in rows[:5]], 'm3u': bool(rows and rows[0][1])}
    finally:
        db.close()


def memory_play():
    for name in ('sysconfig.db', 'song.db'):
        db = sqlite3.connect(f'file:/work/rootfs/usr/data/fiio/db/{name}?mode=ro', uri=True)
        try:
            if db.execute("SELECT 1 FROM sqlite_schema WHERE name = 'MEMORY_PLAY'").fetchone():
                columns = [c[1] for c in db.execute('PRAGMA table_info(MEMORY_PLAY)')]
                return {'database': name, 'rows': [dict(zip(columns, r)) for r in db.execute('SELECT * FROM MEMORY_PLAY')]}
        finally:
            db.close()
    return None


def curlist_mark():
    status, _, headers = call('GET', '/api/stock/song_category_tree/', {'type': 'curlist/song', 'start-pos': '0', 'num-max': '5'})
    return {'status': status, 'mark': headers.get('mark-pos') if headers else None, 'total': headers.get('total-num') if headers else None}


def play_folder(position, seek_ms=None):
    records = [('0100', f'{position:04X}0004' + str(FOLDER))]
    if seek_ms is not None:
        records.append(('0103', f'{seek_ms:08X}'))
    stock(*records, collect=2.5)
    time.sleep(1)
    return current()


# ---- actions ----

def long_lists():
    results = {}
    entries = [rel(track(n)) for n in range(3)]
    log = Path('/work/mq_player.log')
    for count in (2000, 5000):
        path = LISTS/f'long-{count}.m3u'
        write_list(path, [entries[n % 3] for n in range(count)])
        mark = log.stat().st_size
        peer, send, read_until = session()
        started = time.monotonic()
        send('0101', '0004' + str(path))
        got = read_until(playing_path(str(track(0))), 60)
        seconds = round(time.monotonic() - started, 2)
        peer.close()
        lines = [line.strip()[-100:] for line in log.read_bytes()[mark:].decode('utf-8', 'replace').splitlines() if 'parsed' in line]
        results[count] = {'playing_after_s': seconds if got else None, 'queue': queue_hash(), 'stock_log': lines[:2],
                          'bytes': path.stat().st_size}
        stock(('0201', '0001'), collect=2.0)
        results[count]['next'] = current().get('pos_id')
        pause()
    note('long_lists', results)


def visible_folder():
    list_path = VISIBLE/'Visible.m3u'
    write_list(list_path, [rel(track(n)) for n in (2, 1, 0)])
    status, body, _ = call('GET', '/api/stock/localdir/tmp/sdcard/Playlists/', {'start-pos': '0', 'num-max': '50'})
    local = json.loads(body) if status == 200 and body else None
    status2, body2, _ = call('GET', '/api/stock/dir/tmp/sdcard/Playlists/', {'start-pos': '0', 'num-max': '50'})
    transfer = json.loads(body2) if status2 == 200 and body2 else None
    result = {'localdir': {'status': status, 'rows': local}, 'dir': {'status': status2, 'rows': transfer}}
    # By its position in the playback browser, as the device's folder screen and the page's file play do.
    position = next((i for i, row in enumerate(local or []) if str(row.get('name', '')).lower().endswith('.m3u')), None)
    if position is not None:
        stock(('0100', f'{position:04X}0004' + str(VISIBLE)), collect=3.0); time.sleep(1.5)
        result['played_by_position'] = {'now': current(), 'queue': queue_hash()}
        pause()
    stock(('0101', '0004' + str(VISIBLE)), collect=3.0); time.sleep(1.5)
    result['folder_play'] = {'now': current(), 'queue': queue_hash()}
    pause()
    stock(('0101', '0004' + str(list_path)), collect=3.0); time.sleep(1.5)
    result['played_by_path'] = {'now': current(), 'queue': queue_hash()}
    pause()
    total, names = scan()
    db = sqlite3.connect('file:/work/rootfs/usr/data/fiio/db/song.db?mode=ro', uri=True)
    try:
        songs = db.execute("SELECT COUNT(*) FROM SONG WHERE PATH LIKE '%Playlists%'").fetchone()[0]
    finally:
        db.close()
    status3, body3, _ = call('GET', '/api/stock/song_category_tree/', {'type': 'custom', 'start-pos': '0', 'num-max': '50'})
    result['after_scan'] = {'library_total': total, 'songs_under_playlists': songs,
                            'custom_lists': [row.get('name') for row in json.loads(body3)] if status3 == 200 and body3 else status3}
    note('visible_folder', result)


def swaps():
    """From the folder's second track at 12 s, to a list starting at the same track, seeking back to 12 s."""
    swap = LISTS/'swap.m3u'
    write_list(swap, [rel(track(n)) for n in range(3)])
    results = {}
    for variant in ('seek_after_a202', 'seek_at_once', 'seek_after_500ms'):
        play_folder(1, 12000)
        time.sleep(3)
        before = current()
        mark = capture_mark()
        peer, send, read_until = session()
        started = time.monotonic()
        send('0100', '0001' + '0004' + str(swap))
        if variant == 'seek_at_once':
            send('0103', f'{12000:08X}')
            got = read_until(playing_path(str(track(1))), 10)
        else:
            got = read_until(playing_path(str(track(1))), 10)
            if variant == 'seek_after_500ms':
                time.sleep(0.5)
            send('0103', f'{12000:08X}')
        a202_ms = round((time.monotonic() - started) * 1000) if got else None
        time.sleep(4)
        peer.close()
        results[variant] = {'before': {k: before.get(k) for k in ('state', 'song_file_path')}, 'a202_ms': a202_ms,
                            'after': {k: current().get(k) for k in ('state', 'song_file_path', 'is_m3u', 'pos_id')},
                            'heard': heard(mark)}
        pause()
    # The reverse: from a list back to the folder's queue at the same track (the page's switch back).
    play_folder(1, 12000)
    stock(('0100', '0001' + '0004' + str(swap)), ('0103', f'{12000:08X}'), collect=2.0)
    time.sleep(2)
    mark = capture_mark()
    stock(('0100', '0001' + '0004' + str(FOLDER)), collect=0.3)
    stock(('0103', f'{12000:08X}'), collect=3.0)
    results['back_to_folder'] = {'after': {k: current().get(k) for k in ('song_file_path', 'is_m3u')}, 'heard': heard(mark)}
    pause()
    note('swaps', results)


def short_pause():
    """0100 on the current queue after a pause: the same queue, and where playback starts."""
    play_folder(0)
    time.sleep(2)
    pause()
    before = queue_hash()
    time.sleep(20)
    mark = capture_mark()
    stock(('0100', '0001' + '0000'), collect=3.0)
    time.sleep(1)
    note('short_pause', {'queue_before': before, 'queue_after': queue_hash(), 'now': current(), 'heard': heard(mark, 3)})
    pause()


def before_idle():
    note('before_idle', {'now': play_folder(1, 12000)})
    time.sleep(2)
    pause()
    note('paused', {'now': current(), 'queue': queue_hash(), 'memory_play': memory_play(), 'curlist': curlist_mark(),
                    'at': time.time()})


def after_boot():
    deadline = time.monotonic() + 90
    now = {}
    while time.monotonic() < deadline:
        try:
            now = current()
            break
        except OSError:
            time.sleep(2)
    kept = {'now': now, 'queue': queue_hash(), 'memory_play': memory_play(), 'curlist': curlist_mark()}
    mark = capture_mark()
    stock(('0100', '0001' + '0000'), collect=3.0)
    time.sleep(1)
    kept['select_position_1'] = {'now': current(), 'queue': queue_hash(), 'heard': heard(mark, 3)}
    note('after_boot', kept)
    pause()


def wait_off():
    from emulator.runtime.keys import Device
    started = time.monotonic()
    while Device(ROOTFS).processes():
        assert time.monotonic() - started < 420, 'Stock did not power off while paused'
        time.sleep(5)
    note('powered_off', {'after_s': round(time.monotonic() - started), 'since_pause_s': round(time.time() - evidence['paused']['at'])})


def love_rows():
    db = sqlite3.connect('file:/work/rootfs/usr/data/fiio/db/song.db?mode=ro', uri=True)
    try:
        return [list(r) for r in db.execute(
            "SELECT ID, PATH, TRACK, IS_M3U, M3U_PATH FROM MY_LOVE WHERE PATH LIKE '/tmp/sdcard/Queue Research%' ORDER BY ID")]
    finally:
        db.close()


def song_id(path):
    db = sqlite3.connect('file:/work/rootfs/usr/data/fiio/db/song.db?mode=ro', uri=True)
    try:
        row = db.execute('SELECT ID, TRACK, IS_M3U FROM SONG WHERE PATH = ?', (str(path),)).fetchone()
        return list(row) if row else None
    finally:
        db.close()


def favorites():
    import tempfile
    from m3u_playback import SERIAL
    if not track(0).exists():
        make_media()
        scan()
    write_list(LISTS/'fav.m3u', [rel(track(n)) for n in (0, 1)])
    result = {'song': song_id(track(0))}
    # 1. The heart pressed on the player while the list plays.
    stock(('0101', '0004' + str(LISTS/'fav.m3u')), collect=2.5); time.sleep(1)
    stock(('0104', '0001'), collect=2.0)
    result['list_favorite'] = {'rows': love_rows(), 'now': {k: current().get(k) for k in ('love', 'is_m3u')}}
    # 2. The library form of the same file, on a copy of song.db: does UNIQUE(PATH, TRACK) refuse it?
    with tempfile.TemporaryDirectory() as temp:
        copy = Path(temp)/'song.db'
        shutil.copy('/work/rootfs/usr/data/fiio/db/song.db', copy)
        db = sqlite3.connect(copy)
        columns = [c[1] for c in db.execute('PRAGMA table_info(MY_LOVE)') if c[1] != 'ID']
        song_columns = {c[1] for c in db.execute('PRAGMA table_info(SONG)')}
        shared = [c for c in columns if c in song_columns]
        try:
            db.execute(f"INSERT INTO MY_LOVE ({', '.join(shared)}) SELECT {', '.join(shared)} FROM SONG WHERE ID = ?", (result['song'][0],))
            db.commit()
            result['library_form_on_copy'] = 'inserted'
        except sqlite3.DatabaseError as error:
            result['library_form_on_copy'] = f'refused: {error}'
        db.close()
    # 3. The service's favorite_add for the library track (this image's rule: path and track).
    status, body, _ = call('POST', f'/api/favorites/{result["song"][0]}', {'X-Disc-Token': SERIAL,
                                                                            'X-Disc-Request': f'queue-research-{time.time_ns()}'})
    result['favorite_add'] = {'status': status, 'body': body.decode('utf-8', 'replace')[:200], 'rows': love_rows()}
    # 4. The same file from its folder: stock's heart there, and a press of it.
    stock(('0100', '0000' + '0004' + str(FOLDER)), collect=2.5); time.sleep(1)
    before = {k: current().get(k) for k in ('love', 'is_m3u')}
    stock(('0104', '0001'), collect=2.0)
    result['folder_context'] = {'before': before, 'after_press': {k: current().get(k) for k in ('love', 'is_m3u')}, 'rows': love_rows()}
    # 5. Undo: unset in each context; whatever remains is reported.
    stock(('0104', '0000'), collect=2.0)
    stock(('0101', '0004' + str(LISTS/'fav.m3u')), collect=2.5); time.sleep(1)
    stock(('0104', '0000'), collect=2.0)
    pause()
    result['left'] = love_rows()
    note('favorites', result)


def cleanup():
    pause()
    for folder in (FOLDER, LISTS, VISIBLE):
        shutil.rmtree(folder, ignore_errors=True)
    total, _ = scan()
    note('cleaned', {'total': total})


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'run'
    if action == 'wait-off':
        return wait_off()
    status, body, _ = call('GET', '/api/health')
    assert status == 200 and not json.loads(body)['controlActive'], 'Disconnect the browser first'
    if action == 'run':
        evidence.clear()
        make_media()
        total, names = scan()
        note('scanned', {'total': total, 'research': sorted(n for n in names if 'Swap' in n)})
        long_lists()
        visible_folder()
        swaps()
        short_pause()
    elif action == 'before-idle':
        before_idle()
    elif action == 'after-boot':
        after_boot()
    elif action == 'favorites':
        favorites()
    elif action == 'cleanup':
        cleanup()
    else:
        raise SystemExit(f'unknown action {action}')


if __name__ == '__main__':
    main()
