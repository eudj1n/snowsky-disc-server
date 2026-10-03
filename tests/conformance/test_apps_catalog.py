"""The apps this server offers (apps/catalog.json), in the form of snowsky-disc-boot's catalogs
(its docs/contract.md, "Catalogs"): an app is named by its release zip's SHA-256 and size."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEX = set('0123456789abcdef')


class AppsCatalogTests(unittest.TestCase):
    def test_the_catalog_names_each_app_by_its_archive(self):
        data = json.loads((ROOT/'apps/catalog.json').read_text())
        self.assertEqual((data['schema'], data['kind']), (1, 'apps'))
        names = [e['name'] for e in data['entries']]
        self.assertEqual(len(names), len(set(names)), 'an app is listed once')
        for entry in data['entries']:
            with self.subTest(entry['name']):
                source = entry['source']
                self.assertTrue(source['url'] is None or source['url'].startswith('https://'))
                self.assertTrue(len(source['sha256']) == 64 and not set(source['sha256']) - HEX)
                self.assertGreater(source['size'], 0)
                self.assertEqual(entry['api'], 1, 'the API this gateway serves')
                self.assertIsInstance(entry['default'], bool)
                self.assertTrue(entry['version'] and entry['license'])
                self.assertTrue({'date', 'acceptance'} <= set(entry['verified']))
        # The player page is the one app offered by default.
        self.assertEqual([e['name'] for e in data['entries'] if e['default']], ['Disc Player'])


if __name__ == '__main__':
    unittest.main()
