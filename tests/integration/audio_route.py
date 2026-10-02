"""Combined-008 acceptance inside the disposable guest: card audio for the browser, with byte ranges.

Reads the generated FLAC and WAV through /api/media/audio on the MIPS service, whole and in the
ranges a media element asks (the start, a seek into the middle, the tail), and compares every byte
with the card; an unsatisfiable range answers 416. Read-only; disposable media only.
"""
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote

sys.path.insert(0, '/platform/tests/integration')
from gateway_mutation import call  # noqa: E402

ALBUM = Path('/tmp/sdcard/Кириллица Ё й')
OUT = Path('/work/disc-audio.json')


def main():
    summary = {'files': []}
    for track, kind in ((ALBUM/'Second — Ё.flac', 'audio/flac'), (ALBUM/'CI Tone — Проверка.wav', 'audio/wav')):
        data = track.read_bytes()
        route = '/api/media/audio' + quote(str(track), safe='/')
        started = time.monotonic()
        status, body, headers = call('GET', route)
        whole_s = round(time.monotonic() - started, 2)
        assert (status, headers['content-type'], headers['accept-ranges'], body == data) == (200, kind, 'bytes', True), (status, headers)
        middle = len(data) // 2
        for asked, first, last in (('bytes=0-65535', 0, 65535), (f'bytes={middle}-', middle, len(data) - 1), ('bytes=-4096', len(data) - 4096, len(data) - 1)):
            status, body, headers = call('GET', route, {'Range': asked})
            assert (status, headers['content-range'], body) == (206, f'bytes {first}-{last}/{len(data)}', data[first:last + 1]), (asked, status, headers)
        status, _, headers = call('GET', route, {'Range': f'bytes={len(data)}-'})
        assert (status, headers['content-range']) == (416, f'bytes */{len(data)}'), (status, headers)
        summary['files'].append({'type': kind, 'bytes': len(data), 'whole_s': whole_s, 'ranges': 3, 'unsatisfiable': 416})
        print(json.dumps(summary['files'][-1]), flush=True)
    summary['status'] = 'passed'
    OUT.write_text(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
