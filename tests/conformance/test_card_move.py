"""The one-time move of a card to the combined-008 layout (scripts/card_move.py)."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import app_bundle, card_move, disc_database

SECRET = 'not-a-real-token-0123456789abcdefghij'


def line(t, path, s=40, **ctx):
    context = {'type': 3, 'count': 2, 'hash': '0123456789abcdef', 'album': 'Harbor', 'artist': 'Lumen',
               'genre': 'Ambient', 'folder': '/tmp/sdcard/Harbor'}
    context.update(ctx)
    return json.dumps({'v': 1, 't': t, 'path': path, 's': s, 'ctx': context})


class CardMoveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.card = Path(self.temp.name)/'PLAY'
        self.card.mkdir()
        # The combined-007 layout: the page in www/, the history and the token and markers at the root.
        (self.card/'www'/'releases').mkdir(parents=True)
        (self.card/'www'/'active.json').write_text('{"schema":1,"bundle":"0123456789abcdef","api":1}\n')
        (self.card/'DISC_WEB_HISTORY').mkdir()
        (self.card/'DISC_WEB_TOKEN').write_text(SECRET + '\n')
        (self.card/'DISC_WEB_SN_PAIRING').write_text('DISC_WEB_SN_PAIRING\n')
        # A Mac leaves AppleDouble twins beside what it touched; they go with their entries.
        (self.card/'._www').write_bytes(b'\0\5\26\7')
        (self.card/'._DISC_WEB_TOKEN').write_bytes(b'\0\5\26\7')
        (self.card/'._Music').write_bytes(b'\0\5\26\7')
        (self.card/'Music').mkdir()
        (self.card/'Music'/'a.flac').write_bytes(b'\0' * 10)
        self.lines = [
            line(1790000000, '/tmp/sdcard/Harbor/a.flac'),
            line(1790000100, '/tmp/sdcard/Harbor/b.flac', s=12, type=None, hash=None, album=None),
            '{"v":1,"t":"soon","path":"/tmp/sdcard/x.flac","s":1,"ctx":{}}',
            line(1790000200, '', s=3),
            line(1790000300, '/tmp/sdcard/Harbor/c.flac', s=90000),
            line(1790000400, '/tmp/sdcard/Harbor/d.flac', genre='G' * 200),
            'not json',
            line(1790000500, '/tmp/sdcard/Ёж/e.flac', s=31, folder='/tmp/sdcard/Ёж'),
        ]
        self.write_history(self.lines)

    def tearDown(self):
        self.temp.cleanup()

    def write_history(self, lines):
        (self.card/card_move.HISTORY).write_text(''.join(item + '\n' for item in lines))

    def plays(self):
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db:
            return db.execute('SELECT started_at, path, heard_seconds, queue_type, queue_count, queue_hash,'
                              ' queue_album, queue_artist, queue_genre, queue_folder, title FROM plays ORDER BY id').fetchall()

    def test_the_plan_reads_only_and_never_shows_a_token(self):
        before = sorted(str(p.relative_to(self.card)) for p in self.card.rglob('*'))
        result = card_move.plan(self.card)
        self.assertEqual(result['history'], {'valid': 3, 'invalid': 5, 'first': 1790000000, 'last': 1790000500})
        self.assertIsNone(result['database'])
        self.assertIsNone(result['page'])
        self.assertEqual(result['old'], [{'name': '._DISC_WEB_TOKEN', 'kind': 'file'},
                                         {'name': '._www', 'kind': 'file'},
                                         {'name': 'DISC_WEB_HISTORY', 'kind': 'folder'},
                                         {'name': 'DISC_WEB_SN_PAIRING', 'kind': 'file'},
                                         {'name': 'DISC_WEB_TOKEN', 'kind': 'file'},
                                         {'name': 'www', 'kind': 'folder'}])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(sorted(str(p.relative_to(self.card)) for p in self.card.rglob('*')), before)

    def test_the_history_is_imported_once_as_the_service_writes_it(self):
        result = card_move.import_history(self.card)
        self.assertEqual((result['imported'], result['already'], result['invalid'], result['plays']), (3, 0, 5, 3))
        rows = self.plays()
        self.assertEqual(rows[0], (1790000000, '/tmp/sdcard/Harbor/a.flac', 40, 3, 2, '0123456789abcdef',
                                   'Harbor', 'Lumen', 'Ambient', '/tmp/sdcard/Harbor', None))
        self.assertEqual(rows[1][3:7], (None, 2, None, None))
        self.assertEqual(rows[2][1], '/tmp/sdcard/Ёж/e.flac')
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], disc_database.schema_sql()[1])
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(sorted(p.name for p in (self.card/'.disc').iterdir()), ['disc.db'])
        # Again after the new image recorded a play and the old one wrote one more line: only the new line comes in.
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db, db:
            db.execute("INSERT INTO plays(started_at, path, heard_seconds, queue_count, title) VALUES (1790001000, '/tmp/sdcard/new.flac', 35, 0, 'New')")
        self.write_history(self.lines + [line(1790000600, '/tmp/sdcard/Harbor/f.flac')])
        result = card_move.import_history(self.card)
        self.assertEqual((result['imported'], result['already'], result['plays']), (1, 3, 5))
        self.assertEqual(card_move.plan(self.card)['database'], {'schema': disc_database.schema_sql()[1], 'plays': 5,
                                                                 'history_imported': 4})
        # A run with nothing new writes nothing to the card.
        before = (self.card/card_move.DATABASE).stat()
        self.assertEqual(card_move.import_history(self.card)['imported'], 0)
        after = (self.card/card_move.DATABASE).stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))

    def test_a_database_of_another_schema_or_a_link_is_left_alone(self):
        (self.card/'.disc').mkdir()
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db:
            db.execute(f'PRAGMA user_version={disc_database.schema_sql()[1] + 1}')
        with self.assertRaisesRegex(ValueError, 'schema'):
            card_move.import_history(self.card)
        (self.card/card_move.DATABASE).unlink()
        (self.card/card_move.DATABASE).symlink_to(self.card/'Music'/'a.flac')
        with self.assertRaisesRegex(ValueError, 'not a plain file'):
            card_move.import_history(self.card)
        self.assertEqual((self.card/'Music'/'a.flac').read_bytes(), b'\0' * 10)

    def test_an_interrupted_creation_is_replaced_and_an_interrupted_write_is_refused(self):
        # What the first attempt on the owner's exFAT card left (2026-09-28): version 0, one empty table.
        (self.card/'.disc').mkdir()
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db:
            db.execute('CREATE TABLE plays(id INTEGER PRIMARY KEY)')
        self.assertEqual(card_move.plan(self.card)['database'], None)
        self.assertEqual(card_move.import_history(self.card)['plays'], 3)
        self.assertEqual(sorted(p.name for p in (self.card/'.disc').iterdir()), ['disc.db'])
        # A journal beside the file is an interrupted write: only the service resolves it.
        (self.card/'.disc'/'disc.db-journal').write_bytes(b'journal')
        with self.assertRaisesRegex(ValueError, 'journal'):
            card_move.import_history(self.card)
        (self.card/'.disc'/'disc.db-journal').unlink()
        # A staged file from an interrupted move is left for the operator to inspect.
        (self.card/'.disc'/'disc.db.next').write_bytes(b'staged')
        with self.assertRaisesRegex(ValueError, 'disc.db.next'):
            card_move.import_history(self.card)
        self.assertEqual(len(self.plays()), 3)
        (self.card/'.disc'/'disc.db.next').unlink()
        # A version-0 file that holds rows is not ours to replace.
        (self.card/card_move.DATABASE).unlink()
        with closing(sqlite3.connect(self.card/card_move.DATABASE)) as db, db:
            db.execute('CREATE TABLE notes(text TEXT)')
            db.execute("INSERT INTO notes VALUES ('keep')")
        with self.assertRaisesRegex(ValueError, 'schema 0'):
            card_move.import_history(self.card)

    def test_the_old_layout_goes_only_after_the_history_is_in(self):
        with self.assertRaisesRegex(ValueError, 'import-history'):
            card_move.remove_old(self.card)
        card_move.import_history(self.card)
        self.write_history(self.lines + [line(1790000600, '/tmp/sdcard/Harbor/f.flac')])
        with self.assertRaisesRegex(ValueError, '1 history lines'):
            card_move.remove_old(self.card)
        card_move.import_history(self.card)
        result = card_move.remove_old(self.card)
        self.assertEqual([entry['name'] for entry in result['removed']],
                         ['._DISC_WEB_TOKEN', '._www', 'DISC_WEB_HISTORY', 'DISC_WEB_SN_PAIRING', 'DISC_WEB_TOKEN', 'www'])
        self.assertEqual(sorted(p.name for p in self.card.iterdir()), ['._Music', '.disc', 'Music'])
        self.assertEqual(len(self.plays()), 4)

    def test_disc_player_moves_into_apps_and_the_old_releases_go_last(self):
        # combined-009: the page is an app in Apps/, the catalogs in .disc/catalog; .disc/www goes once it is there.
        source = Path(self.temp.name)/'dist'
        source.mkdir()
        (source/'index.html').write_text('<!doctype html><title>Page</title>')
        files = app_bundle.build_app(source, version='2026.09.29')
        app_bundle.zip_app(files, 'Disc Player', Path(self.temp.name)/'player.zip')
        (self.card/'.disc'/'www'/'releases').mkdir(parents=True)
        (self.card/'.disc'/'www'/'active.json').write_text('{"schema":1,"bundle":"0123456789abcdef","api":1}\n')
        command = [sys.executable, str(ROOT/'scripts'/'card_move.py')]
        refused = subprocess.run(command + ['install-app', '--card', str(self.card), '--app', str(Path(self.temp.name)/'player.zip')],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('--confirm-card-write', refused.stderr)
        early = subprocess.run(command + ['remove-www', '--card', str(self.card), '--confirm-card-write'], capture_output=True, text=True)
        self.assertEqual((early.returncode, early.stderr), (1, 'Apps/Disc Player has no index.html yet; run install-app first\n'))
        subprocess.run(command + ['install-app', '--card', str(self.card), '--app', str(Path(self.temp.name)/'player.zip'),
                                  '--confirm-card-write'], capture_output=True, text=True, check=True)
        subprocess.run(command + ['install-catalog', '--card', str(self.card), '--commands', '--confirm-card-write'],
                       capture_output=True, text=True, check=True)
        plan = card_move.plan(self.card)
        self.assertEqual((plan['page'], plan['app'], plan['catalog']),
                         ('0123456789abcdef', {'version': '2026.09.29'}, ['commands.json', 'hosted.json', 'queries.json', 'store.json']))
        done = subprocess.run(command + ['remove-www', '--card', str(self.card), '--confirm-card-write'],
                              capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(done.stdout)['removed'], '.disc/www')
        self.assertFalse((self.card/'.disc'/'www').exists())
        self.assertTrue((self.card/'Apps'/'Disc Player'/'index.html').is_file())


if __name__ == '__main__':
    unittest.main()
