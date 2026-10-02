"""Combined-008 acceptance inside the disposable guest: a CUE image counts per track.

A three-track FLAC image with its CUE sheet (20 s per track, generated with sox) is scanned into
stock's library and played as an album: the observer records one play per track, each with its
title. Then the second track is disliked through the store (path and CUE title): played again,
stock skips it on the service's "next", confirmed by the next track's name, and it is not
recorded. The probe is removed and the library rescanned. Disposable media only.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/conformance')
sys.path.insert(0, '/platform/tests/integration')
from gateway_mutation import call  # noqa: E402
from store_skip import control, guarded  # noqa: E402
from guest_checks import service_log  # noqa: E402
from trash_card import scan  # noqa: E402

PROBE = Path('/tmp/sdcard/CUE Probe')
IMAGE = PROBE/'Image.flac'
TITLES = ('Part One', 'Part Two', 'Part Three')
OUT = Path('/work/disc-cue.json')
summary = {'steps': []}


def step(name, **fields):
    summary['steps'].append({'name': name, **fields}); print(name, json.dumps(fields, ensure_ascii=False), flush=True)


def make_probe():
    shutil.rmtree(PROBE, ignore_errors=True); PROBE.mkdir()
    parts = []
    for n, frequency in enumerate((440, 660, 880)):
        part = PROBE/f'{n}.wav'
        subprocess.run(['sox', '-n', '-r', '44100', '-b', '16', '-c', '2', str(part), 'synth', '20', 'sine', str(frequency), 'vol', '0.3'], check=True)
        parts.append(str(part))
    subprocess.run(['sox', *parts, str(IMAGE)], check=True)
    for part in parts: Path(part).unlink()
    sheet = ['PERFORMER "CUE Artist"', 'TITLE "CUE Album"', 'FILE "Image.flac" WAVE']
    for n, title in enumerate(TITLES):
        sheet += [f'  TRACK {n + 1:02} AUDIO', f'    TITLE "{title}"', '    PERFORMER "CUE Artist"', f'    INDEX 01 00:{20 * n:02}:00']
    (PROBE/'Image.cue').write_text('\r\n'.join(sheet) + '\r\n')


def history():
    status, body, _ = call('GET', '/api/history'); assert status == 200, status
    return json.loads(body)['records']


def play_album(seconds):
    before = len(history())
    name = 'CUE Album'
    control(('0101' + f'{12 + len(name.encode()):04X}' + '0003' + name, 'a202', 0))
    time.sleep(seconds)
    return [(r['path'], r['title']) for r in history()[before:]]


def main():
    make_probe()
    total, names = scan()
    assert all(title in names for title in TITLES), names
    step('scanned', total=total)
    plays = play_album(68)
    assert plays == [(str(IMAGE), title) for title in TITLES], plays
    step('per_track', titles=[title for _, title in plays])
    status, body, _ = guarded('PUT', '/api/store/disliked/record', {'path': str(IMAGE), 'title': 'Part Two', 'at': int(time.time())})
    assert status == 200, (status, body[:120])
    # The boot layer keeps the gateway's output (capped by emptying): the skip lines after this mark.
    before = [line for line in service_log().splitlines() if 'Skip rule' in line]
    plays = play_album(62)
    after = [line for line in service_log().splitlines() if 'Skip rule' in line]
    log = after[len(before):] if len(after) >= len(before) else after
    assert plays == [(str(IMAGE), 'Part One'), (str(IMAGE), 'Part Three')], plays
    assert log == ['Skip rule: skipped to the next track'], log
    step('cue_track_skipped', recorded=[title for _, title in plays], log=log)
    status, body, _ = guarded('DELETE', '/api/store/disliked/record?path=' + quote(str(IMAGE)) + '&title=' + quote('Part Two'))
    assert status == 200 and json.loads(body)['deleted'] is True, body
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
        scan()
