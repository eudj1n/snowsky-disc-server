"""Reviewed hosted pages (2026-09-30).

Data selected by firmware version, written with the image's catalogs and to a
card's .disc/catalog as hosted.json. The service admits these exact https
origins from the browser and answers them with CORS (device/src/origins.c
checks the same rules), so a public site is added or moved with a card
catalog. No wildcard: every origin is one site.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import firmware_profile
from scripts.origins_catalog import NAME, require, valid_origin

CATALOGS = firmware_profile.PROFILES / 'hosted'
MAX_PAGES = 4


def load_hosted(version=None, directory=CATALOGS):
    profile = firmware_profile.load_profile(version)
    path = Path(directory) / f'v{profile["version"]}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed hosted catalog first')
    catalog = json.loads(path.read_text())
    require(isinstance(catalog, dict) and set(catalog) == {'schema_version', 'api', 'version', 'description', 'pages'}
            and catalog['schema_version'] == 1 and catalog['api'] == 1 and catalog['version'] == profile['version'],
            'Invalid hosted catalog identity/schema')
    pages = catalog['pages']
    require(isinstance(pages, dict) and len(pages) <= MAX_PAGES, f'At most {MAX_PAGES} hosted pages')
    seen = set()
    for name, entry in pages.items():
        require(NAME.fullmatch(name), f'page {name}: invalid name')
        require(isinstance(entry, dict) and set(entry) == {'origin', 'purpose'}, f'page {name}: unexpected fields')
        require(valid_origin(entry['origin']) and '*' not in entry['origin'], f'page {name}: not an exact https origin')
        require(entry['origin'] not in seen, f'page {name}: duplicate origin')
        seen.add(entry['origin'])
        require(isinstance(entry['purpose'], str) and entry['purpose'], f'page {name}: purpose is required')
    return catalog


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def hosted_data(profile, catalog):
    require(catalog['version'] == profile['version'], 'Hosted catalog and firmware profile disagree')
    published = dict(catalog, profile_sha256=firmware_profile.fingerprint(profile), hosted_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


if __name__ == '__main__':
    catalog = load_hosted(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'version': catalog['version'], 'origins': [p['origin'] for p in catalog['pages'].values()],
                      'hosted_sha256': fingerprint(catalog)}, indent=2))
