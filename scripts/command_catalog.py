"""Reviewed command catalog: what a client may ask the gateway to forward.

The catalog is data selected by firmware version, published with every SD
bundle as commands.json. The service implements the guards (owner, token,
request IDs, pacing, denylist); the catalog describes each admitted TCP record
and stock HTTP route. Nothing here touches a device.
"""
import hashlib
import json
from pathlib import Path
import re
from re import _parser as sre_parse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import firmware_profile

CATALOGS = firmware_profile.PROFILES / 'commands'
TAG = re.compile(r'[0-9a-f]{4}\Z')
KINDS = {'read', 'mutation'}
CLASSES = {'identity', 'settings', 'state', 'mode', 'sound', 'library', 'control', 'favorite',
           'seek', 'volume', 'selection', 'scan', 'artwork', 'files', 'upload', 'playlist'}
METHODS = {'GET', 'POST', 'DELETE'}
BODIES = {'none', 'json', 'raw'}
# Never admitted, whatever a card catalog says. Mirrors the service denylist.
BUILTIN_DENIED_RECORDS = ('0621', '0800')
BUILTIN_DENIED_HTTP = (('DELETE', '/file/'),)
MAX_RECORD = 65535
MAX_UPLOAD = 1024 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


ERE_FORBIDDEN = re.compile(r'\(\?|\\[dDwWsSbBxuAZ]|\\[0-9]')
# musl's TRE regcomp expands every counted repetition into copies before
# building its automaton, and a long optional chain such as .{0,255} costs
# memory quadratic in the bound: 2.8 MiB transient on the MIPS player, 10.5 MiB
# for ([^/]{1,255}/)*[^/]{1,255}. Counted bounds stay small; express a longer
# exact bound in chunks, e.g. .{0,255} as (.{16}){0,15}.{0,15}.
MAX_COUNT = 16
MAX_EXPANDED = 1024


def expanded_positions(parsed, what):
    """Character positions musl TRE builds after expanding counted repetitions."""
    total = 0
    for op, arg in parsed:
        if op in (sre_parse.LITERAL, sre_parse.NOT_LITERAL, sre_parse.ANY, sre_parse.IN):
            total += 1
        elif op is sre_parse.SUBPATTERN:
            total += expanded_positions(arg[3], what)
        elif op is sre_parse.BRANCH:
            total += sum(expanded_positions(branch, what) for branch in arg[1])
        elif op in (sre_parse.MAX_REPEAT, sre_parse.MIN_REPEAT):
            low, high, body = arg
            unbounded = high == sre_parse.MAXREPEAT
            require(low <= MAX_COUNT and (unbounded or high <= MAX_COUNT),
                    f'{what}: counted repetitions are limited to {{{MAX_COUNT}}} (musl expands them at compile '
                    'time); write longer bounds in chunks such as (X{16}){0,15}X{0,15}')
            require(high <= 1 or body.getwidth()[0] > 0, f'{what}: repeated groups must not match the empty string')
            total += (max(low, 1) if unbounded else high) * expanded_positions(body, what)
        elif op is not sre_parse.AT:
            raise ValueError(f'{what}: unsupported construct {op}')
    return total


def pattern(value, what):
    """Patterns must be valid for both Python and the device's POSIX ERE (musl regcomp),
    and cheap for musl to compile: the gateway compiles them for every catalog load."""
    require(isinstance(value, str) and len(value) <= 512, f'{what}: pattern must be a bounded string')
    require(not ERE_FORBIDDEN.search(value), f'{what}: use POSIX ERE syntax only (no (?:, \\d, \\x escapes)')
    try:
        compiled = re.compile(value, re.DOTALL)
    except re.error as error:
        raise ValueError(f'{what}: invalid pattern ({error})') from None
    positions = expanded_positions(sre_parse.parse(value, re.DOTALL), what)
    require(positions <= MAX_EXPANDED, f'{what}: pattern expands to {positions} positions (limit {MAX_EXPANDED})')
    return compiled


def validate_record(tag, entry):
    require(TAG.fullmatch(tag) and tag[0] == '0', f'record {tag}: tag must be four lowercase hex digits starting with 0')
    require(isinstance(entry, dict), f'record {tag}: entry must be an object')
    require(set(entry) == {'name', 'kind', 'class', 'payload', 'reply', 'silent_ok', 'timeout_ms', 'pacing_ms',
                           'max_bytes', 'notes', 'evidence'}, f'record {tag}: unexpected fields')
    require(re.fullmatch(r'[a-z][a-z0-9_]{1,40}', entry['name']), f'record {tag}: invalid name')
    require(entry['kind'] in KINDS and entry['class'] in CLASSES, f'record {tag}: invalid kind/class')
    pattern(entry['payload'], f'record {tag} payload')
    reply = entry['reply']
    require(reply is None or (TAG.fullmatch(reply) and reply[0] == 'a'), f'record {tag}: reply must be an axxx tag or null')
    require(entry['kind'] == 'read' or entry['pacing_ms'] > 0, f'record {tag}: mutations need pacing')
    require(entry['kind'] == 'mutation' or reply is not None, f'record {tag}: reads need an expected reply tag')
    require(type(entry['silent_ok']) is bool, f'record {tag}: silent_ok must be boolean')
    require(type(entry['timeout_ms']) is int and 100 <= entry['timeout_ms'] <= 60000, f'record {tag}: timeout out of range')
    require(type(entry['pacing_ms']) is int and 0 <= entry['pacing_ms'] <= 10000, f'record {tag}: pacing out of range')
    require(type(entry['max_bytes']) is int and 8 <= entry['max_bytes'] <= MAX_RECORD, f'record {tag}: max_bytes out of range')
    require(isinstance(entry['notes'], str) and isinstance(entry['evidence'], str) and entry['evidence'],
            f'record {tag}: evidence is required')


def validate_route(route):
    require(isinstance(route, dict) and set(route) == {'name', 'method', 'path', 'kind', 'class', 'headers', 'required_headers',
                                                       'body', 'max_body_bytes', 'notes', 'evidence'}, 'http route: unexpected fields')
    name = route['name']
    require(re.fullmatch(r'[a-z][a-z0-9_]{1,40}', name), 'http route: invalid name')
    require(route['method'] in METHODS and route['kind'] in KINDS and route['class'] in CLASSES, f'route {name}: invalid method/kind/class')
    require(route['path'].startswith('/'), f'route {name}: path pattern must be absolute')
    pattern(route['path'], f'route {name} path')
    # The service refuses any request with a query before consulting the catalog.
    require('\\?' not in route['path'], f'route {name}: query strings are never forwarded')
    require(isinstance(route['headers'], dict), f'route {name}: headers must be an object')
    for header, value in route['headers'].items():
        require(re.fullmatch(r'[a-z][a-z0-9_-]{0,40}', header), f'route {name}: invalid header name {header}')
        pattern(value, f'route {name} header {header}')
    require(all(h in route['headers'] for h in route['required_headers']), f'route {name}: required headers must be described')
    require(route['body'] in BODIES and type(route['max_body_bytes']) is int and 0 <= route['max_body_bytes'] <= MAX_UPLOAD,
            f'route {name}: invalid body description')
    require((route['body'] == 'none') == (route['max_body_bytes'] == 0), f'route {name}: body kind and size disagree')
    require(route['method'] != 'GET' or route['body'] == 'none', f'route {name}: GET carries no body')
    require(route['kind'] == 'mutation' or route['method'] == 'GET', f'route {name}: non-GET routes are mutations')
    require(isinstance(route['evidence'], str) and route['evidence'], f'route {name}: evidence is required')


# Data-level mutations the service implements itself; the catalog can only admit
# them by name. Their statements are reviewed code, never card data.
DATA_MUTATIONS = {'favorite_add'}


def validate_data(entry):
    require(isinstance(entry, dict) and set(entry) == {'name', 'kind', 'class', 'pacing_ms', 'notes', 'evidence'},
            'data mutation: unexpected fields')
    require(entry['name'] in DATA_MUTATIONS, f'data mutation {entry["name"]}: not implemented by the service')
    require(entry['kind'] == 'mutation' and isinstance(entry['class'], str) and re.fullmatch(r'[a-z_]{1,16}', entry['class'])
            and type(entry['pacing_ms']) is int and 250 <= entry['pacing_ms'] <= 10000
            and all(isinstance(entry[k], str) and entry[k] for k in ('notes', 'evidence')), f'data mutation {entry["name"]}: invalid')


def load_catalog(version=None, directory=CATALOGS):
    profile = firmware_profile.load_profile(version)
    path = Path(directory) / f'v{profile["version"]}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed command catalog first')
    catalog = json.loads(path.read_text())
    require(isinstance(catalog, dict) and catalog.get('schema_version') == 1 and catalog.get('api') == 1
            and catalog.get('version') == profile['version'], 'Invalid catalog identity/schema')
    require(set(catalog) - {'data'} == {'schema_version', 'api', 'version', 'description', 'records', 'http', 'denied'},
            'Unexpected catalog fields')
    records, routes, denied = catalog['records'], catalog['http'], catalog['denied']
    require(isinstance(records, dict) and records and isinstance(routes, list) and isinstance(denied, dict), 'Invalid catalog sections')
    names = set()
    for tag, entry in records.items():
        validate_record(tag, entry)
        require(entry['name'] not in names, f'record {tag}: duplicate name')
        names.add(entry['name'])
    for route in routes:
        validate_route(route)
        require(route['name'] not in names, f'route {route["name"]}: duplicate name')
        names.add(route['name'])
    for entry in catalog.get('data', []):
        validate_data(entry)
        require(entry['name'] not in names, f'data {entry["name"]}: duplicate name')
        names.add(entry['name'])
    require(set(denied) == {'records', 'http'} and isinstance(denied['records'], dict) and isinstance(denied['http'], list),
            'Invalid denied section')
    for tag in BUILTIN_DENIED_RECORDS:
        require(tag in denied['records'] and tag not in records, f'built-in denied record {tag} must be listed and never admitted')
    for tag, reason in denied['records'].items():
        require(TAG.fullmatch(tag) and tag not in records and isinstance(reason, str) and reason, f'denied record {tag}: invalid')
    for rule in denied['http']:
        require(isinstance(rule, dict) and rule.get('method') in METHODS and isinstance(rule.get('path'), str)
                and isinstance(rule.get('reason'), str) and rule['reason'], 'denied http rule: invalid')
        pattern(rule['path'], 'denied http path')
        for header, value in rule.get('headers', {}).items():
            pattern(value, f'denied http header {header}')
    for method, prefix in BUILTIN_DENIED_HTTP:
        require(any(r['method'] == method and r['path'].startswith(prefix) for r in denied['http']),
                f'built-in denied route {method} {prefix} must be listed')
        require(not any(r['method'] == method and r['path'].startswith(prefix) for r in routes),
                f'built-in denied route {method} {prefix} must never be admitted')
    return catalog


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def commands_data(profile, catalog):
    """Bytes of commands.json for a published bundle: the reviewed catalog plus its provenance."""
    require(catalog['version'] == profile['version'], 'Catalog and firmware profile disagree')
    published = dict(catalog, profile_sha256=firmware_profile.fingerprint(profile), catalog_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def admits_record(catalog, tag, payload):
    """Whether the catalog describes this record; the service applies the same rule."""
    entry = catalog['records'].get(tag.lower())
    if entry is None or tag.lower() in catalog['denied']['records']:
        return None
    if 8 + len(payload.encode('utf-8')) > entry['max_bytes'] or not re.compile(entry['payload'], re.DOTALL).fullmatch(payload):
        return None
    return entry


def admits_route(catalog, method, path, headers):
    """Whether the catalog describes this stock HTTP request; denials win over admissions."""
    # Path hygiene precedes every pattern: no traversal, empty, backslash or NUL components.
    if '\\' in path or '\x00' in path or '//' in path or any(part in ('.', '..') for part in path.split('/')):
        return None
    lowered = {k.lower(): v for k, v in headers.items()}
    for rule in catalog['denied']['http']:
        if rule['method'] == method and re.compile(rule['path'], re.DOTALL).fullmatch(path) and all(
                h in lowered and re.compile(p, re.DOTALL).fullmatch(lowered[h]) for h, p in rule.get('headers', {}).items()):
            return None
    for route in catalog['http']:
        if route['method'] != method or not re.compile(route['path'], re.DOTALL).fullmatch(path):
            continue
        if any(h not in lowered for h in route['required_headers']):
            continue
        if all(h not in lowered or re.compile(p, re.DOTALL).fullmatch(lowered[h]) for h, p in route['headers'].items()):
            return route
    return None


if __name__ == '__main__':
    catalog = load_catalog(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'version': catalog['version'], 'records': len(catalog['records']), 'http': len(catalog['http']),
                      'mutations': sum(e['kind'] == 'mutation' for e in catalog['records'].values())
                      + sum(r['kind'] == 'mutation' for r in catalog['http']), 'catalog_sha256': fingerprint(catalog)}, indent=2))
