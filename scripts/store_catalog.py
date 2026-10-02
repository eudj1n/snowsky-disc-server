"""Reviewed collections of the service's card database (the store, combined-008).

Data selected by firmware version, published with every SD bundle as
store.json. Each collection names its record key, fields and their types,
limits and indexed fields; the gateway checks every record against it and
offers the same fixed operations for every collection (get, put, delete,
list, count, batch). A collection marked "skip" makes the service skip a
playing track found in it. Nothing here opens a device database.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import command_catalog, firmware_profile

CATALOGS = firmware_profile.PROFILES / 'store'
NAME = re.compile(r'[a-z][a-z0-9_]{1,40}\Z')
TYPES = {'text', 'int', 'bool', 'path', 'json'}
MAX_COLLECTIONS = 16
MAX_FIELDS = 16
MAX_KEY = 3
MAX_RECORDS = 100000
MAX_RECORD_BYTES = 16384
MAX_TEXT = 4096
# The gateway's bounds on one call; the page keeps within them.
MAX_BATCH = 100
MAX_LIST = 500


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_field(collection, name, field):
    what = f'collection {collection} field {name}'
    require(NAME.fullmatch(name), f'{what}: invalid name')
    require(isinstance(field, dict) and field.get('type') in TYPES, f'{what}: unknown type')
    kind = field['type']
    allowed = {'type', 'required'} | {'text': {'max_length', 'pattern'}, 'int': {'min', 'max'}, 'bool': set(),
                                      'path': set(), 'json': {'max_length'}}[kind]
    require(set(field) <= allowed, f'{what}: unexpected fields')
    require(type(field.get('required', False)) is bool, f'{what}: required must be a boolean')
    if kind in ('text', 'json'):
        limit = MAX_TEXT if kind == 'text' else MAX_RECORD_BYTES
        require(type(field.get('max_length')) is int and 1 <= field['max_length'] <= limit, f'{what}: max_length out of range')
    if kind == 'int':
        require(type(field.get('min')) is int and type(field.get('max')) is int and
                -2 ** 53 < field['min'] <= field['max'] < 2 ** 53, f'{what}: int needs min <= max')
    if 'pattern' in field:
        require('(?' not in field['pattern'] and '\\' not in field['pattern'], f'{what}: pattern must be plain POSIX ERE')
        command_catalog.pattern(field['pattern'], what)


def validate_collection(name, entry):
    require(NAME.fullmatch(name), f'collection {name}: invalid name')
    require(isinstance(entry, dict) and set(entry) <= {'description', 'key', 'fields', 'index', 'max_records',
                                                       'max_record_bytes', 'skip'} and
            {'description', 'key', 'fields', 'max_records', 'max_record_bytes'} <= set(entry), f'collection {name}: unexpected fields')
    fields = entry['fields']
    require(isinstance(fields, dict) and 0 < len(fields) <= MAX_FIELDS, f'collection {name}: 1..{MAX_FIELDS} fields')
    for field_name, field in fields.items():
        validate_field(name, field_name, field)
    key = entry['key']
    require(isinstance(key, list) and 0 < len(key) <= MAX_KEY and len(set(key)) == len(key) and
            all(k in fields and fields[k]['type'] in ('text', 'int', 'path') for k in key),
            f'collection {name}: key must be 1..{MAX_KEY} distinct text, int or path fields')
    require(fields[key[0]].get('required') is True, f'collection {name}: the first key field is required')
    index = entry.get('index', [])
    require(isinstance(index, list) and len(set(index)) == len(index) and
            all(i in fields and fields[i]['type'] != 'json' for i in index), f'collection {name}: index names scalar fields')
    require(type(entry['max_records']) is int and 1 <= entry['max_records'] <= MAX_RECORDS, f'collection {name}: max_records out of range')
    require(type(entry['max_record_bytes']) is int and 64 <= entry['max_record_bytes'] <= MAX_RECORD_BYTES,
            f'collection {name}: max_record_bytes out of range')
    if entry.get('skip', False):
        require(entry['skip'] is True and fields[key[0]]['type'] == 'path' and
                key[1:] in ([], ['title']) and fields.get('title', {'type': 'text'})['type'] == 'text',
                f'collection {name}: skip needs a track key (path, optionally title)')


def load_store(version=None, directory=CATALOGS):
    profile = firmware_profile.load_profile(version)
    path = Path(directory) / f'v{profile["version"]}.json'
    require(path.is_file(), 'Unknown firmware; add a reviewed store catalog first')
    catalog = json.loads(path.read_text())
    require(isinstance(catalog, dict) and set(catalog) == {'schema_version', 'api', 'version', 'description', 'collections'}
            and catalog['schema_version'] == 1 and catalog['api'] == 1 and catalog['version'] == profile['version'],
            'Invalid store catalog identity/schema')
    collections = catalog['collections']
    require(isinstance(collections, dict) and 0 < len(collections) <= MAX_COLLECTIONS, 'No collections')
    for name, entry in collections.items():
        validate_collection(name, entry)
    return catalog


def fingerprint(catalog):
    return hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def store_data(profile, catalog):
    require(catalog['version'] == profile['version'], 'Store catalog and firmware profile disagree')
    published = dict(catalog, profile_sha256=firmware_profile.fingerprint(profile), store_sha256=fingerprint(catalog))
    return (json.dumps(published, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


if __name__ == '__main__':
    catalog = load_store(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({'version': catalog['version'], 'collections': sorted(catalog['collections']),
                      'store_sha256': fingerprint(catalog)}, indent=2))
