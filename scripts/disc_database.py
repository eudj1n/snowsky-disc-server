"""The service's card database schema (.disc/disc.db, combined-008) for tools and tests.

The service creates and upgrades the file itself; its statements live in
device/src/database.c and are read from there, so tools never drift from
the device. Used by the query catalog tests and by the one-time move of the
owner's card (the JSON-lines history of combined-007 into the plays table).
"""
from pathlib import Path
import re
import sqlite3

SOURCE = Path(__file__).resolve().parents[1] / 'device/src/database.c'


def schema_sql():
    """The SCHEMA string of database.c and its version, as the service runs them."""
    source = SOURCE.read_text()
    body = re.search(r'static const char SCHEMA\[\] =\s*((?:"(?:[^"\\]|\\.)*"\s*)+);', source)
    version = re.search(r'#define DISC_DATABASE_VERSION (\d+)', (SOURCE.parent / 'database.h').read_text())
    if not body or not version:
        raise ValueError('database.c no longer declares its schema as expected')
    return ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', body.group(1))), int(version.group(1))


def create(path):
    """A database file with the current schema, as the service would create it."""
    sql, version = schema_sql()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.executescript(sql)
        db.execute(f'PRAGMA user_version={version}')
    return Path(path)
