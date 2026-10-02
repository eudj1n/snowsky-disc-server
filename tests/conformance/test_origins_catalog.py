"""Reviewed external origins: only https origins the page's policy may safely name."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import firmware_profile, origins_catalog as oc


class OriginsCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = oc.load_origins()

    def test_the_reviewed_providers_extend_connect_and_image_sources(self):
        self.assertEqual(oc.policy(self.catalog),
                         "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://musicbrainz.org "
                         "https://coverartarchive.org https://archive.org https://*.archive.org https://lrclib.net "
                         "https://www.wikidata.org https://commons.wikimedia.org https://upload.wikimedia.org "
                         "https://thumb.wikimedia.org https://webservice.fanart.tv https://assets.fanart.tv; "
                         "img-src 'self' https://coverartarchive.org https://archive.org "
                         "https://*.archive.org https://upload.wikimedia.org https://thumb.wikimedia.org; "
                         "frame-ancestors 'none'")
        self.assertEqual(oc.policy(dict(self.catalog, origins={})),
                         "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")

    def test_published_origins_json_carries_provenance(self):
        profile = firmware_profile.load_profile()
        data = json.loads(oc.origins_data(profile, self.catalog))
        self.assertEqual((data['profile_sha256'], data['origins_sha256']), (firmware_profile.fingerprint(profile), oc.fingerprint(self.catalog)))

    def test_origins_are_checked_character_by_character(self):
        for good in ('https://musicbrainz.org', 'https://*.archive.org', 'https://a-b.example.org:8443'):
            self.assertTrue(oc.valid_origin(good), good)
        for bad in ('http://musicbrainz.org', 'https://MusicBrainz.org', 'https://*.org', 'https://org', 'https://a.org/',
                    'https://a.org/path', 'https://a.org:0', 'https://a.org:70000', 'https://a.org; script-src *',
                    "https://a.org 'unsafe-inline'", 'https://-a.org', 'https://a-.org', 'https://a..org', 'https://*',
                    '*', 'https://a.org\r\nX: y', 'https://' + 'a' * 64 + '.org', 'https://a.*.org', 'data:', 'blob:'):
            self.assertFalse(oc.valid_origin(bad), bad)

    def test_tampered_catalogs_are_rejected(self):
        def rejected(mutate, message):
            broken = copy.deepcopy(self.catalog); mutate(broken)
            with tempfile.TemporaryDirectory() as d:
                (Path(d)/'v2.57.json').write_text(json.dumps(broken))
                with self.assertRaisesRegex(ValueError, message):
                    oc.load_origins(directory=Path(d))
        first = lambda c: c['origins']['musicbrainz']
        rejected(lambda c: first(c).__setitem__('origin', 'https://a.org; script-src *'), 'https origin')
        rejected(lambda c: first(c).__setitem__('directives', ['script-src']), 'directives')
        rejected(lambda c: first(c).__setitem__('directives', []), 'directives')
        rejected(lambda c: first(c).__setitem__('origin', 'https://lrclib.net'), 'duplicate')
        rejected(lambda c: first(c).__setitem__('note', 'x'), 'unexpected fields')
        rejected(lambda c: c['origins'].update({f'extra{n}': {'origin': f'https://e{n}.org', 'directives': ['img-src'],
                                                               'purpose': 'x'} for n in range(13)}), 'At most 16')


if __name__ == '__main__':
    unittest.main()
