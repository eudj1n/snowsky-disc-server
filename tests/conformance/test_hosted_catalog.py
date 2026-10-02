"""Reviewed hosted pages: exact https origins only, bounded, with provenance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import firmware_profile, hosted_catalog as hc


class HostedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = hc.load_hosted()

    def test_the_prototype_site_is_listed(self):
        self.assertEqual([p['origin'] for p in self.catalog['pages'].values()], ['https://eudj1n.github.io'])

    def test_published_hosted_json_carries_provenance(self):
        profile = firmware_profile.load_profile()
        data = json.loads(hc.hosted_data(profile, self.catalog))
        self.assertEqual((data['profile_sha256'], data['hosted_sha256']),
                         (firmware_profile.fingerprint(profile), hc.fingerprint(self.catalog)))
        self.assertEqual(data['pages'], self.catalog['pages'])

    def check(self, catalog):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/f'v{catalog["version"]}.json').write_text(json.dumps(catalog))
            return hc.load_hosted(directory=folder)

    def test_only_exact_https_origins(self):
        for origin in ('http://eudj1n.github.io', 'https://*.github.io', 'https://eudj1n.github.io/', 'https://Eudj1n.github.io',
                       'https://github', 'https://eudj1n.github.io/player', 'https://eudj1n.github.io:0'):
            bad = copy.deepcopy(self.catalog)
            bad['pages']['github_pages_prototype']['origin'] = origin
            with self.assertRaises(ValueError, msg=origin):
                self.check(bad)
        self.assertEqual(self.check(self.catalog)['pages'], self.catalog['pages'])

    def test_bounds_and_fields(self):
        many = copy.deepcopy(self.catalog)
        many['pages'] = {f'site_{n}': {'origin': f'https://site{n}.example', 'purpose': 'x'} for n in range(5)}
        with self.assertRaises(ValueError):
            self.check(many)
        twice = copy.deepcopy(self.catalog)
        twice['pages']['again'] = dict(twice['pages']['github_pages_prototype'])
        with self.assertRaises(ValueError):
            self.check(twice)
        extra = copy.deepcopy(self.catalog)
        extra['pages']['github_pages_prototype']['path'] = '/player/'
        with self.assertRaises(ValueError):
            self.check(extra)


if __name__ == '__main__':
    unittest.main()
