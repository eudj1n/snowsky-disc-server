#!/usr/bin/env python3
"""The one-time moves of a card to the combined-008 and combined-009 layouts (the owner's card).

Combined-007 kept its page in `www/`, the play history in
`DISC_WEB_HISTORY/plays.jsonl` and its token and switches in `DISC_WEB_*`
files at the card root; combined-008 keeps everything in `.disc/` (owner's
decision, 2026-09-28: no transition in the service, the owner is the only
user). The steps, run on a card mounted on this computer:

  plan            read-only: what is there and what each step would do
  install-app     combined-009: an app's zip (Disc Player) into Apps/ (app_bundle)
  install-catalog combined-009: the reviewed catalogs into .disc/catalog
                  (with --commands for an engineering image)
  import-history  plays.jsonl into .disc/disc.db; idempotent, so it runs
                  again after the new image is installed to catch the last
                  plays the old one wrote
  remove-old      www/ and every DISC_WEB_* entry at the card root, only
                  once every valid history line is in the database; run it
                  after the new image is installed and accepted, since an
                  older image still needs its token and markers
  remove-www      combined-009: the page releases of combined-008 in
                  .disc/www, only once Apps/Disc Player has an index.html;
                  after combined-009 is installed and accepted

Each writing step needs --confirm-card-write after the operator checked the
mount. Contents of the old token and marker files are never read or printed.
Nothing here touches the player itself.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import app_bundle, disc_database

HISTORY = Path('DISC_WEB_HISTORY')/'plays.jsonl'
DATABASE = Path('.disc')/'disc.db'
OLD_PREFIX = 'DISC_WEB_'
# The history route's limits (device/src/history.c): rows outside them are
# never served, so they are not imported either.
LATEST = 4102444800
MAX_SECONDS = 86400
MAX_QUEUE_ROWS = 20000
TEXT_BYTES = {'path': 1024, 'album': 256, 'artist': 256, 'genre': 128, 'folder': 512}
KEEP_ROWS = 100000
MAX_HISTORY_BYTES = 64 * 1024 * 1024


def real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def real_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def check_card(card: Path):
    if not real_dir(card):
        raise ValueError('The card mount is not a directory')
    for part in (card/'.disc', card/'DISC_WEB_HISTORY'):
        if part.is_symlink():
            raise ValueError(f'{part.name} on the card is a symbolic link; inspect it first')


def text(value, limit):
    """A string the service would serve: non-empty for a path, valid UTF-8 without NUL, shorter than its field."""
    if not isinstance(value, str) or '\0' in value:
        return None
    try:
        size = len(value.encode())
    except UnicodeEncodeError:
        return None
    return value if size < limit else None


def integer(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def parse_line(line: str):
    """One combined-007 history line as a plays row, or None when the service could not have written it."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get('v') != 1 or not isinstance(record.get('ctx'), dict):
        return None
    started, seconds = integer(record.get('t'), 0, LATEST), integer(record.get('s'), 0, MAX_SECONDS)
    path = text(record.get('path'), TEXT_BYTES['path'])
    if started is None or seconds is None or not path:
        return None
    context = record['ctx']
    queue_type = context.get('type')
    if queue_type is not None and integer(queue_type, 0, 255) is None:
        return None
    count = integer(context.get('count'), 0, MAX_QUEUE_ROWS)
    if count is None:
        return None
    digest = context.get('hash')
    if digest is not None and not (isinstance(digest, str) and len(digest) == 16 and
                                   all(c in '0123456789abcdef' for c in digest)):
        return None
    fields = []
    for name in ('album', 'artist', 'genre', 'folder'):
        value = context.get(name)
        if value is not None and text(value, TEXT_BYTES[name]) is None:
            return None
        fields.append(value)
    return (started, path, seconds, queue_type, count, digest, *fields)


def read_history(card: Path):
    """The valid rows of plays.jsonl in file order, and how many lines were not valid."""
    source = card/HISTORY
    if not real_file(source):
        return None, 0
    if source.lstat().st_size > MAX_HISTORY_BYTES:
        raise ValueError('plays.jsonl is larger than any history the old image could write; inspect it first')
    rows, invalid = [], 0
    with source.open('rb') as handle:
        for raw in handle:
            try:
                line = raw.decode()
            except UnicodeDecodeError:
                invalid += 1
                continue
            if not line.strip():
                continue
            row = parse_line(line)
            if row is None:
                invalid += 1
            else:
                rows.append(row)
    return rows, invalid


def working_copy(card: Path, work: Path, create: bool):
    """The card database as a local working copy at the service's schema, or None when absent.

    SQLite cannot be trusted to write a card mounted by this computer: macOS
    answered "attempt to write a readonly database" on the owner's exFAT card
    after the first statement (2026-09-28). So the tool reads the card file
    into `work`, changes the copy there and puts the finished file back with
    store(). A version-0 file whose tables are all empty is what an
    interrupted creation leaves and counts as absent; a journal next to the
    file means an interrupted write, which only the service may resolve.
    """
    source, local = card/DATABASE, work/'disc.db'
    expected = disc_database.schema_sql()[1]
    staged = card/'.disc'/'disc.db.next'
    if staged.exists() or staged.is_symlink():
        raise ValueError('.disc/disc.db.next is already on the card (an interrupted move); inspect it first')
    if source.exists() or source.is_symlink():
        if not real_file(source):
            raise ValueError('.disc/disc.db is not a plain file; inspect it first')
        journal = card/'.disc'/'disc.db-journal'
        if journal.exists() or journal.is_symlink():
            raise ValueError('A journal lies next to .disc/disc.db (an interrupted write); let the service open it first')
        local.write_bytes(source.read_bytes())
        db = sqlite3.connect(local)
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            db.close()
            raise ValueError('.disc/disc.db does not pass quick_check; inspect it first')
        tables = [name for (name,) in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")]
        empty = all(db.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 0 for name in tables)
        if version == 0 and empty:
            db.close()
            local.unlink()
        elif version != expected:
            db.close()
            raise ValueError(f'.disc/disc.db is at schema {version}, this tool writes {expected}; '
                             'let the matching service open it first')
        else:
            return db
    if not create:
        return None
    disc_database.create(local)
    return sqlite3.connect(local)


def store(card: Path, local: Path):
    """Puts a finished database file on the card: written beside, read back, then renamed over the old one."""
    folder = card/'.disc'
    folder.mkdir(exist_ok=True)
    if not real_dir(folder):
        raise ValueError('.disc on the card is not a folder; inspect it first')
    staged = folder/'disc.db.next'
    if staged.exists() or staged.is_symlink():
        raise ValueError('.disc/disc.db.next is already on the card (an interrupted move); inspect it first')
    data = local.read_bytes()
    app_bundle.write_new(staged, data)
    os.replace(staged, folder/'disc.db')
    os.sync()
    if (folder/'disc.db').read_bytes() != data:
        raise ValueError('.disc/disc.db readback mismatch')


def imported(db, rows):
    """How many of the rows the database already holds (the same start time and path)."""
    keys = set(db.execute('SELECT started_at, path FROM plays'))
    return sum((row[0], row[1]) in keys for row in rows)


def old_entries(card: Path):
    """The combined-007 layout at the card root: www/ and every DISC_WEB_* entry, by name and kind
    only, with the AppleDouble twins (._name) a Mac left beside them."""
    def old(name):
        return name == 'www' or name.startswith(OLD_PREFIX)
    entries = []
    for entry in sorted(card.iterdir(), key=lambda p: p.name):
        if not (old(entry.name) or (entry.name.startswith('._') and old(entry.name[2:]))):
            continue
        info = entry.lstat()
        kind = 'link' if stat.S_ISLNK(info.st_mode) else 'folder' if stat.S_ISDIR(info.st_mode) else 'file'
        entries.append({'name': entry.name, 'kind': kind})
    return entries


def plan(card: Path):
    check_card(card)
    rows, invalid = read_history(card)
    work = tempfile.TemporaryDirectory()
    db = working_copy(card, Path(work.name), create=False)
    try:
        database = None
        if db is not None:
            database = {'schema': disc_database.schema_sql()[1],
                        'plays': db.execute('SELECT count(*) FROM plays').fetchone()[0],
                        'history_imported': imported(db, rows) if rows else 0}
    finally:
        if db is not None:
            db.close()
        work.cleanup()
    active = card/'.disc'/'www'/'active.json'
    page = json.loads(active.read_text()).get('bundle') if real_file(active) else None
    player = card/'Apps'/app_bundle.DEFAULT_APP
    app = None
    if real_file(player/'index.html'):
        about = player/'app.json'
        app = {'version': json.loads(about.read_text()).get('version') if real_file(about) else None}
    catalog = sorted(p.name for p in (card/'.disc'/'catalog').iterdir()) if real_dir(card/'.disc'/'catalog') else None
    history = None if rows is None else {'valid': len(rows), 'invalid': invalid,
                                         'first': min((r[0] for r in rows), default=None),
                                         'last': max((r[0] for r in rows), default=None)}
    return {'card': str(card), 'page': page, 'app': app, 'catalog': catalog, 'database': database, 'history': history,
            'old': old_entries(card), 'physical_device_accessed': False}


def import_history(card: Path):
    check_card(card)
    rows, invalid = read_history(card)
    if rows is None:
        raise ValueError('No DISC_WEB_HISTORY/plays.jsonl on the card')
    rows = sorted(rows, key=lambda row: row[0])[-KEEP_ROWS:]
    with tempfile.TemporaryDirectory() as work:
        db = working_copy(card, Path(work), create=True)
        try:
            keys = set(db.execute('SELECT started_at, path FROM plays'))
            fresh = [row for row in rows if (row[0], row[1]) not in keys]
            db.execute('PRAGMA journal_mode=DELETE')
            with db:
                db.executemany('INSERT INTO plays(started_at, path, heard_seconds, queue_type, queue_count, queue_hash,'
                               ' queue_album, queue_artist, queue_genre, queue_folder) VALUES (?,?,?,?,?,?,?,?,?,?)', fresh)
            total = db.execute('SELECT count(*) FROM plays').fetchone()[0]
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('The updated database does not pass quick_check; nothing was written')
        finally:
            db.close()
        # Nothing new (a second run after the history is in): the card is left as it is.
        if fresh:
            store(card, Path(work)/'disc.db')
    return {'imported': len(fresh), 'already': len(rows) - len(fresh), 'invalid': invalid, 'plays': total,
            'physical_device_accessed': False}


def remove_old(card: Path):
    check_card(card)
    rows, _ = read_history(card)
    if rows:
        with tempfile.TemporaryDirectory() as work:
            db = working_copy(card, Path(work), create=False)
            if db is None:
                raise ValueError('The history is not imported yet; run import-history first')
            # Only the newest rows the service keeps are imported, so only they must be there.
            kept = sorted(rows, key=lambda row: row[0])[-KEEP_ROWS:]
            try:
                missing = len(kept) - imported(db, kept)
            finally:
                db.close()
        if missing:
            raise ValueError(f'{missing} history lines are not in the database yet; run import-history first')
    removed = []
    for entry in old_entries(card):
        path = card/entry['name']
        if entry['kind'] == 'folder':
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(entry)
    os.sync()
    return {'removed': removed, 'physical_device_accessed': False}


def remove_www(card: Path):
    """Combined-009: the page releases of combined-008, once Disc Player is an app on the card."""
    check_card(card)
    if not real_file(card/'Apps'/app_bundle.DEFAULT_APP/'index.html'):
        raise ValueError('Apps/Disc Player has no index.html yet; run install-app first')
    www = card/'.disc'/'www'
    removed = real_dir(www)
    if removed:
        shutil.rmtree(www)
        os.sync()
    return {'removed': '.disc/www' if removed else None, 'physical_device_accessed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    for name in ('plan', 'install-app', 'install-catalog', 'import-history', 'remove-old', 'remove-www'):
        step = sub.add_parser(name)
        step.add_argument('--card', type=Path, required=True, help='Mounted card root, verified by the operator')
        if name != 'plan':
            step.add_argument('--confirm-card-write', action='store_true')
        if name == 'install-app':
            step.add_argument('--app', type=Path, required=True, help="The app's zip (or folder)")
        if name == 'install-catalog':
            step.add_argument('--commands', action='store_true', help='Also commands.json (an engineering image reads it)')
    args = p.parse_args()
    if args.action != 'plan' and not args.confirm_card_write:
        p.error(f'{args.action} writes to the card; pass --confirm-card-write after verifying the mount')
    try:
        if args.action == 'plan':
            result = plan(args.card)
        elif args.action == 'install-app':
            check_card(args.card)
            result = app_bundle.install(args.app, args.card)
        elif args.action == 'install-catalog':
            check_card(args.card)
            result = app_bundle.install_catalog(args.card, args.commands)
        elif args.action == 'remove-www':
            result = remove_www(args.card)
        elif args.action == 'import-history':
            result = import_history(args.card)
        else:
            result = remove_old(args.card)
    except ValueError as error:
        p.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
