"""Reviewed store catalog invariants: collections, keys, fields and limits are declared and bounded."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import firmware_profile, store_catalog as sc


class StoreCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = sc.load_store()

    def test_disliked_tracks_are_a_track_keyed_skip_collection(self):
        disliked = self.catalog['collections']['disliked']
        self.assertEqual(disliked['key'], ['path', 'title'])
        self.assertEqual(disliked['fields']['path'], {'type': 'path', 'required': True})
        self.assertIs(disliked['skip'], True)
        self.assertEqual(disliked['index'], ['at'])

    def test_published_store_json_carries_provenance(self):
        profile = firmware_profile.load_profile()
        data = json.loads(sc.store_data(profile, self.catalog))
        self.assertEqual((data['profile_sha256'], data['store_sha256']), (firmware_profile.fingerprint(profile), sc.fingerprint(self.catalog)))
        self.assertEqual(data['collections'], self.catalog['collections'])

    def test_tampered_catalogs_are_rejected(self):
        def rejected(mutate, message):
            broken = copy.deepcopy(self.catalog); mutate(broken)
            with tempfile.TemporaryDirectory() as d:
                (Path(d)/'v2.57.json').write_text(json.dumps(broken))
                with self.assertRaisesRegex(ValueError, message):
                    sc.load_store(directory=Path(d))
        disliked = lambda c: c['collections']['disliked']
        rejected(lambda c: c['collections'].__setitem__('Bad-Name', disliked(c)), 'invalid name')
        rejected(lambda c: disliked(c)['fields'].__setitem__('mood', {'type': 'blob'}), 'unknown type')
        rejected(lambda c: disliked(c)['fields']['title'].pop('max_length'), 'max_length')
        rejected(lambda c: disliked(c)['fields']['title'].__setitem__('max_length', 5000), 'max_length')
        rejected(lambda c: disliked(c)['fields']['at'].__setitem__('min', 10 ** 20), 'min <= max')
        rejected(lambda c: disliked(c)['fields']['title'].__setitem__('pattern', '(?i)x'), 'POSIX ERE')
        rejected(lambda c: disliked(c).__setitem__('key', ['title']), 'first key field is required')
        rejected(lambda c: disliked(c).__setitem__('key', ['path', 'missing']), 'key must be')
        rejected(lambda c: disliked(c)['fields'].__setitem__('notes', {'type': 'json', 'max_length': 100}) or
                 disliked(c).__setitem__('index', ['notes']), 'index names scalar fields')
        rejected(lambda c: disliked(c).__setitem__('max_records', 10 ** 6), 'max_records')
        rejected(lambda c: disliked(c).__setitem__('max_record_bytes', 10), 'max_record_bytes')
        rejected(lambda c: disliked(c).__setitem__('key', ['path', 'artist']), 'skip needs a track key')
        rejected(lambda c: disliked(c).__setitem__('sql', 'SELECT 1'), 'unexpected fields')
        rejected(lambda c: c.__setitem__('collections', {}), 'No collections')


if __name__ == '__main__':
    unittest.main()
