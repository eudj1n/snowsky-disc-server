"""The external origins an app names (combined-009; owner, 2026-10-23).

An app on the card carries its own origins.json beside index.html: the
https origins its page may connect to or load images from. The service adds
them to the Content-Security-Policy of the pages it serves for that app, so
a provider comes and goes with the app's own release, never with the
server's. The rules here and in device/src/origins.c are the same, and
strict, because the header is built from card data: one bad entry rejects
the file, and the app stays same-origin.

Until combined-008 the list was the server's (firmware/origins, bound to the
firmware profile); that catalog is gone. This module keeps the rules, checks
an app's file and tells the policy the service will build from it.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import firmware_profile

NAME = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
# https only; a wildcard only as the first label, with at least two labels after it; no path.
ORIGIN = re.compile(rf'https://(?:\*\.)?{LABEL}(?:\.{LABEL})+(?::[0-9]{{1,5}})?\Z')
DIRECTIVES = ('connect-src', 'img-src')
MAX_ORIGINS = 32
MAX_ORIGIN = 100
MAX_BYTES = 64 * 1024
REQUIRED = {'schema_version', 'api', 'origins'}
OPTIONAL = {'version', 'description', 'profile_sha256', 'origins_sha256'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def valid_origin(origin):
    if not isinstance(origin, str) or len(origin) > MAX_ORIGIN or not ORIGIN.fullmatch(origin):
        return False
    host = origin[len('https://'):].split(':')[0]
    port = origin[len('https://'):].split(':')[1] if ':' in origin[len('https://'):] else None
    return (not host.startswith('*.') or host.count('.') >= 2) and (port is None or 1 <= int(port) <= 65535)


def check_origins(catalog):
    """An app's origins.json as a dict, checked by the service's rules; returns it."""
    require(isinstance(catalog, dict) and REQUIRED <= set(catalog) and set(catalog) <= REQUIRED | OPTIONAL,
            'origins.json: schema_version, api and origins, optionally version, description and provenance')
    require(catalog['schema_version'] == 1 and catalog['api'] == 1, 'origins.json: schema_version 1, api 1')
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


def load_origins(path):
    """An app's origins.json from a file, checked."""
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= MAX_BYTES, f'{path}: not an origins.json the service reads')
    return check_origins(json.loads(path.read_text(encoding='utf-8')))


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def origins_data(catalog, profile=None):
    """The file as it goes beside index.html; with a firmware profile, bound to it (an image's file, combined-008)."""
    published = dict(check_origins(catalog))
    if profile is not None:
        published.update(profile_sha256=firmware_profile.fingerprint(profile), origins_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def policy(catalog):
    """The page's Content-Security-Policy with these origins, exactly as the service builds it."""
    extra = {d: [e['origin'] for e in catalog['origins'].values() if d in e['directives']] for d in DIRECTIVES}
    img = ("; img-src 'self' " + ' '.join(extra['img-src'])) if extra['img-src'] else ''
    connect = ' '.join(["'self'", *extra['connect-src']])
    return (f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src {connect}{img}; "
            "frame-ancestors 'none'")


if __name__ == '__main__':
    catalog = load_origins(sys.argv[1] if len(sys.argv) > 1 else 'origins.json')
    print(json.dumps({'origins': len(catalog['origins']), 'policy': policy(catalog), 'origins_sha256': fingerprint(catalog)}, indent=2))
