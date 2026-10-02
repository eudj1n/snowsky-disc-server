"""Reviewed read-only queries over the stock SQLite databases (the gateway data level).

Since combined-008 the database file "@disc" names the service's own card
database (.disc/disc.db), read through the service's lock and checks.

Data selected by firmware version, published with every SD bundle as
queries.json. The gateway executes only these statements with bounded,
validated parameters against read-only connections. Nothing here opens a
device database.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import command_catalog, firmware_profile

CATALOGS = firmware_profile.PROFILES / 'queries'
NAME = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
FORBIDDEN = re.compile(r'\b(ATTACH|DETACH|PRAGMA|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|VACUUM|REINDEX|WITH|LOAD_EXTENSION|RANDOMBLOB|WRITEFILE|READFILE)\b', re.I)
MAX_ROWS = 1000
MAX_SQL = 2000
SERVICE_DB = '@disc'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_param(query, p):
    require(isinstance(p, dict) and set(p) >= {'name', 'type'} and NAME.fullmatch(p['name']), f'query {query}: invalid parameter')
    if p['type'] == 'int':
        require(set(p) == {'name', 'type', 'min', 'max'} and type(p['min']) is int and type(p['max']) is int and p['min'] <= p['max'],
                f'query {query}: int parameter needs min/max')
    elif p['type'] == 'text':
        require(set(p) == {'name', 'type', 'pattern', 'max_length'} and isinstance(p['pattern'], str)
                and type(p['max_length']) is int and 1 <= p['max_length'] <= 255, f'query {query}: text parameter needs pattern/max_length')
        require('(?' not in p['pattern'] and '\\' not in p['pattern'], f'query {query}: text pattern must be plain POSIX ERE')
        # Same musl compile-cost rule as the command catalog; max_length bounds the value.
        command_catalog.pattern(p['pattern'], f'query {query} parameter {p["name"]}')
    else:
        raise ValueError(f'query {query}: unsupported parameter type')


def validate_query(name, entry, databases):
    require(NAME.fullmatch(name), f'query {name}: invalid name')
    require(isinstance(entry, dict) and set(entry) == {'db', 'sql', 'params', 'max_rows', 'enums', 'notes', 'evidence'}, f'query {name}: unexpected fields')
    require(entry['db'] in databases, f'query {name}: unknown database')
    sql = entry['sql']
    require(isinstance(sql, str) and 0 < len(sql) <= MAX_SQL and sql.lstrip().upper().startswith('SELECT '), f'query {name}: must be a single SELECT')
    require(';' not in sql and '--' not in sql and '/*' not in sql, f'query {name}: no statement separators or comments')
    require(not FORBIDDEN.search(sql), f'query {name}: forbidden keyword')
    require(isinstance(entry['params'], list) and sql.count('?') == len(entry['params']), f'query {name}: placeholder count differs from params')
    names = set()
    for p in entry['params']:
        validate_param(name, p)
        require(p['name'] not in names, f'query {name}: duplicate parameter')
        names.add(p['name'])
    require(type(entry['max_rows']) is int and 1 <= entry['max_rows'] <= MAX_ROWS, f'query {name}: max_rows out of range')
    require(isinstance(entry['enums'], dict), f'query {name}: enums must be an object')
    require(isinstance(entry['evidence'], str) and entry['evidence'], f'query {name}: evidence is required')


def load_queries(version=None, directory=CATALOGS):
    profile = firmware_profile.load_profile(version)
    path = Path(directory) / f'v{profile["version"]}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed query catalog first')
    catalog = json.loads(path.read_text())
    require(isinstance(catalog, dict) and set(catalog) == {'schema_version', 'api', 'version', 'description', 'databases', 'queries'}
            and catalog['schema_version'] == 1 and catalog['api'] == 1 and catalog['version'] == profile['version'], 'Invalid query catalog identity/schema')
    databases = catalog['databases']
    require(isinstance(databases, dict) and databases and
            all(NAME.fullmatch(k) and (re.fullmatch(r'[a-z]+\.db', v) or v == SERVICE_DB) for k, v in databases.items()),
            'Invalid database map')
    require(isinstance(catalog['queries'], dict) and catalog['queries'], 'No queries')
    for name, entry in catalog['queries'].items():
        validate_query(name, entry, databases)
    return catalog


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def queries_data(profile, catalog):
    require(catalog['version'] == profile['version'], 'Query catalog and firmware profile disagree')
    published = dict(catalog, profile_sha256=firmware_profile.fingerprint(profile), queries_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def bind(catalog, name, values):
    """Validated parameter list for one query, or None; the gateway applies the same rules."""
    entry = catalog['queries'].get(name)
    if entry is None:
        return None
    bound = []
    for p in entry['params']:
        raw = values.get(p['name'])
        if raw is None:
            return None
        if p['type'] == 'int':
            if not re.fullmatch(r'-?\d{1,10}', raw):
                return None
            v = int(raw)
            if not p['min'] <= v <= p['max']:
                return None
            bound.append(v)
        else:
            if len(raw) > p['max_length'] or not re.fullmatch(p['pattern'], raw):
                return None
            bound.append(raw)
    return bound


if __name__ == '__main__':
    catalog = load_queries(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'version': catalog['version'], 'queries': sorted(catalog['queries']), 'queries_sha256': fingerprint(catalog)}, indent=2))
