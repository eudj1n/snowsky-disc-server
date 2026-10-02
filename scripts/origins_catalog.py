"""Reviewed external origins of the page (combined-008).

Data selected by firmware version, published with every SD bundle as
origins.json. The service adds these origins to the Content-Security-Policy
of the page it serves (connect-src, img-src), so enrichment providers come
and go with a card release. The rules here and in device/src/origins.c are
the same, and strict, because the header is built from card data.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import firmware_profile

CATALOGS = firmware_profile.PROFILES / 'origins'
NAME = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
# https only; a wildcard only as the first label, with at least two labels after it; no path.
ORIGIN = re.compile(rf'https://(?:\*\.)?{LABEL}(?:\.{LABEL})+(?::[0-9]{{1,5}})?\Z')
DIRECTIVES = ('connect-src', 'img-src')
MAX_ORIGINS = 16
MAX_ORIGIN = 100


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_origin(origin):
    if not isinstance(origin, str) or len(origin) > MAX_ORIGIN or not ORIGIN.fullmatch(origin):
        return False
    host = origin[len('https://'):].split(':')[0]
    port = origin[len('https://'):].split(':')[1] if ':' in origin[len('https://'):] else None
    return (not host.startswith('*.') or host.count('.') >= 2) and (port is None or 1 <= int(port) <= 65535)


def load_origins(version=None, directory=CATALOGS):
    profile = firmware_profile.load_profile(version)
    path = Path(directory) / f'v{profile["version"]}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed origins catalog first')
    catalog = json.loads(path.read_text())
    require(isinstance(catalog, dict) and set(catalog) == {'schema_version', 'api', 'version', 'description', 'origins'}
            and catalog['schema_version'] == 1 and catalog['api'] == 1 and catalog['version'] == profile['version'],
            'Invalid origins catalog identity/schema')
    origins = catalog['origins']
    require(isinstance(origins, dict) and len(origins) <= MAX_ORIGINS, f'At most {MAX_ORIGINS} origins')
    seen = set()
    for name, entry in origins.items():
        require(NAME.fullmatch(name), f'origin {name}: invalid name')
        require(isinstance(entry, dict) and set(entry) == {'origin', 'directives', 'purpose'}, f'origin {name}: unexpected fields')
        require(valid_origin(entry['origin']), f'origin {name}: not an https origin the policy may name')
        require(entry['origin'] not in seen, f'origin {name}: duplicate origin')
        seen.add(entry['origin'])
        directives = entry['directives']
        require(isinstance(directives, list) and directives and len(set(directives)) == len(directives) and
                all(d in DIRECTIVES for d in directives), f'origin {name}: directives are connect-src and/or img-src')
        require(isinstance(entry['purpose'], str) and entry['purpose'], f'origin {name}: purpose is required')
    return catalog


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def origins_data(profile, catalog):
    require(catalog['version'] == profile['version'], 'Origins catalog and firmware profile disagree')
    published = dict(catalog, profile_sha256=firmware_profile.fingerprint(profile), origins_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def policy(catalog):
    """The page's Content-Security-Policy with these origins, exactly as the service builds it."""
    extra = {d: [e['origin'] for e in catalog['origins'].values() if d in e['directives']] for d in DIRECTIVES}
    img = ("; img-src 'self' " + ' '.join(extra['img-src'])) if extra['img-src'] else ''
    connect = ' '.join(["'self'", *extra['connect-src']])
    return (f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src {connect}{img}; "
            "frame-ancestors 'none'")


if __name__ == '__main__':
    catalog = load_origins(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'version': catalog['version'], 'policy': policy(catalog), 'origins_sha256': fingerprint(catalog)}, indent=2))
