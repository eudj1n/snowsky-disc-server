"""Combined-008/009 acceptance inside the disposable guest: the diagnostics document and the image's app.

Reads /api/about from the MIPS service (version, build, supervision, the image's identity file,
the card database, restarts and messages), then moves the card's Disc Player aside: the image's
own copy is served at / and About says so; the card's app is put back. Disposable guest only.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/platform/tests/integration')
from gateway_mutation import call  # noqa: E402

CARD_APP = Path('/tmp/sdcard/Apps/Disc Player')
ASIDE = Path('/tmp/sdcard/Apps/.Disc Player.aside')
OUT = Path('/work/disc-about.json')


def about():
    status, body, _ = call('GET', '/api/about')
    assert status == 200, (status, body[:200])
    return json.loads(body)


def main():
    doc = about()
    assert doc['service']['version'] == '0.9.0' and doc['service']['supervised'] is True, doc['service']
    assert doc['image']['variant'] == 'guest' and doc['database']['state'] in ('ok', 'absent'), doc
    card_page = doc['page']
    assert card_page['source'] == 'card', card_page
    CARD_APP.rename(ASIDE)
    try:
        status, body, _ = call('GET', '/')
        image_page = about()['page']
        assert status == 200 and image_page == {'source': 'image', 'app': 'Disc Player', 'version': None}, image_page
        assert b'NATIVE PROBE' in body, body[:200]
    finally:
        ASIDE.rename(CARD_APP)
    assert about()['page'] == card_page
    summary = {'service': doc['service'], 'image': doc['image'], 'database': doc['database'],
               'restarts': len(doc['restarts']), 'messages': len(doc['log']), 'card_page': card_page,
               'image_page': image_page, 'status': 'passed'}
    OUT.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
