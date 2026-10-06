"""The diagnostics document and the default app on the disposable guest (scripts/emulator.py).

Reads /api/about from the gateway the boot layer runs as its package (version, build, project
link, supervision, the player's language, the boot layer's decision, package, interfaces and
menu, the card database, messages), then
moves the card's Disc Player aside: with no app "/" leads to the manager and About says
there is no page; the card's app is put back. Disposable guest only.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, '/platform/tests/integration')
from gateway_mutation import call  # noqa: E402
from guest_checks import CARD, MANAGER_PORT  # noqa: E402

CARD_APP = CARD/'Apps/Disc Player'
ASIDE = CARD/'Apps/.Disc Player.aside'
OUT = Path('/work/disc-about.json')


def about():
    status, body, _ = call('GET', '/api/about')
    assert status == 200, (status, body[:200])
    return json.loads(body)


def main():
    doc = about()
    assert doc['service']['version'] == '0.9.0' and doc['service']['supervised'] is True, doc['service']
    assert doc['service']['homepage'] == 'https://github.com/eudj1n/snowsky-disc-server', doc['service']
    # The player's language as the page's code, from stock's settings through the reviewed query.
    status, body, _ = call('GET', '/api/data/system_settings')
    settings = json.loads(body)
    index = dict(zip(settings['columns'], settings['rows'][0]))['LANGUAGE']
    languages = ['zh-Hans', 'zh-Hant', 'en', 'ja', 'ko', 'es', 'it', 'de', 'fr', 'ru']
    assert doc['player'] == {'language': languages[index] if 0 <= index < len(languages) else None}, (doc['player'], index)
    boot = doc['boot']
    assert boot['decision']['mode'] == 'platform' and boot['service']['name'] == 'disc-server', boot
    assert 'ui' in boot and 'menu' in boot, sorted(boot)
    assert boot['service']['state'] in ('ready', 'confirmed'), boot
    assert doc['image'] is None and doc['database']['state'] in ('ok', 'absent'), doc
    assert doc['ports']['manager'] == MANAGER_PORT, doc['ports']
    card_page = doc['page']
    assert card_page['source'] == 'card', card_page
    CARD_APP.rename(ASIDE)
    try:
        status, _, headers = call('GET', '/')
        empty_page = about()['page']
        assert status == 302 and headers.get('location', '').endswith(f':{MANAGER_PORT}/'), (status, headers)
        assert empty_page == {'source': None, 'app': None, 'version': None, 'homepage': None}, empty_page
    finally:
        ASIDE.rename(CARD_APP)
    assert about()['page'] == card_page
    summary = {'service': doc['service'], 'player': doc['player'], 'boot': boot, 'database': doc['database'], 'messages': len(doc['log']),
               'card_page': card_page, 'without_app': empty_page, 'status': 'passed'}
    OUT.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
