"""Reviewed query catalog invariants: every query runs on the stock schema, parameters are bounded."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tests/conformance'))
from scripts import disc_database, query_catalog as qc, firmware_profile
import stock_schema


class QueryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = qc.load_queries()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name); stock_schema.create(self.dir)
        disc_database.create(self.dir/'disc.db')

    def database(self, entry):
        file = self.catalog['databases'][entry['db']]
        return self.dir/('disc.db' if file == qc.SERVICE_DB else file)

    def test_every_query_executes_against_the_stock_schema_and_respects_max_rows(self):
        sample = {'limit': '2', 'offset': '0', 'id': '7'}
        for name, entry in self.catalog['queries'].items():
            bound = qc.bind(self.catalog, name, sample)
            self.assertIsNotNone(bound, name)
            con = sqlite3.connect(f'file:{self.database(entry)}?mode=ro', uri=True)
            rows = con.execute(entry['sql'], bound).fetchall(); con.close()
            self.assertLessEqual(len(rows), entry['max_rows'], name)

    def test_the_counts_survive_a_dropped_queue_table(self):
        """Stock drops LIST_SONG_0 when a scan removes a queued file (guest evidence, 2026-09-29): the counts
        still answer and say so, and queue_state tells a client not to read the queue."""
        def run(name):
            con = sqlite3.connect(f'file:{self.dir/"song.db"}?mode=ro', uri=True); con.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in con.execute(self.catalog['queries'][name]['sql'])]
            finally:
                con.close()
        self.assertEqual(run('library_summary')[0]['queue_table'], 1)
        self.assertEqual(run('queue_state'), [{'present': 1}])
        con = sqlite3.connect(self.dir/'song.db'); con.execute('DROP TABLE LIST_SONG_0'); con.commit(); con.close()
        self.assertEqual(run('library_summary')[0]['queue_table'], 0)
        self.assertEqual(run('queue_state'), [{'present': 0}])
        with self.assertRaisesRegex(sqlite3.OperationalError, 'no such table'):
            run('queue')

    def test_language_and_settings_come_back_as_declared(self):
        con = sqlite3.connect(f'file:{self.dir/"sysconfig.db"}?mode=ro', uri=True); con.row_factory = sqlite3.Row
        row = con.execute(self.catalog['queries']['system_settings']['sql']).fetchone(); con.close()
        self.assertEqual(self.catalog['queries']['system_settings']['enums']['LANGUAGE'][row['LANGUAGE']], 'ru')
        self.assertEqual((row['BATTERY'], row['POWER_SAVE'], row['MAX_VOL']), (87, 300, 120))

    def test_parameter_binding_is_bounded(self):
        c = self.catalog
        self.assertEqual(qc.bind(c, 'tracks', {'limit': '500', 'offset': '0'}), [500, 0])
        self.assertIsNone(qc.bind(c, 'tracks', {'limit': '501', 'offset': '0'}))
        self.assertIsNone(qc.bind(c, 'tracks', {'limit': '10'}))
        self.assertIsNone(qc.bind(c, 'tracks', {'limit': '1 OR 1', 'offset': '0'}))
        self.assertIsNone(qc.bind(c, 'track_by_id', {'id': '0'}))
        self.assertIsNone(qc.bind(c, 'missing', {}))
        self.assertEqual(qc.bind(c, 'system_settings', {}), [])

    def test_published_queries_json_carries_provenance(self):
        profile = firmware_profile.load_profile()
        data = json.loads(qc.queries_data(profile, self.catalog))
        self.assertEqual((data['profile_sha256'], data['queries_sha256']), (firmware_profile.fingerprint(profile), qc.fingerprint(self.catalog)))

    def test_tampered_catalogs_are_rejected(self):
        def rejected(mutate, message):
            broken = copy.deepcopy(self.catalog); mutate(broken)
            with tempfile.TemporaryDirectory() as d:
                (Path(d)/'v2.57.json').write_text(json.dumps(broken))
                with self.assertRaisesRegex(ValueError, message):
                    qc.load_queries(directory=Path(d))
        rejected(lambda c: c['queries']['queue'].__setitem__('sql', 'DELETE FROM LIST_SONG_0'), 'single SELECT')
        rejected(lambda c: c['queries']['queue'].__setitem__('sql', 'SELECT 1; DROP TABLE SONG'), 'separators')
        rejected(lambda c: c['queries']['queue'].__setitem__('sql', 'SELECT * FROM SONG WHERE ID = ?'), 'placeholder count')
        rejected(lambda c: c['queries']['queue'].__setitem__('sql', 'SELECT load_extension(1)'), 'forbidden keyword')
        rejected(lambda c: c['queries']['queue'].__setitem__('max_rows', 5000), 'max_rows')
        rejected(lambda c: c['queries']['queue'].__setitem__('db', 'dic'), 'unknown database')
        rejected(lambda c: c['queries']['tracks']['params'][0].pop('max'), 'min/max')


if __name__ == '__main__':
    unittest.main()
