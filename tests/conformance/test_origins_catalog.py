"""An app's external origins: only https origins the page's policy may safely name (combined-009)."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import firmware_profile, origins_catalog as oc

# An app's file as Disc Player ships it (snowsky-disc-player release/origins.json), shortened.
ORIGINS = {
    'schema_version': 1, 'api': 1, 'description': 'x',
    'origins': {
        'musicbrainz': {'origin': 'https://musicbrainz.org', 'directives': ['connect-src'], 'purpose': 'metadata'},
        'coverartarchive': {'origin': 'https://coverartarchive.org', 'directives': ['connect-src', 'img-src'], 'purpose': 'covers'},
        'archive': {'origin': 'https://*.archive.org', 'directives': ['connect-src', 'img-src'], 'purpose': 'cover hosts'},
        'lrclib': {'origin': 'https://lrclib.net', 'directives': ['connect-src'], 'purpose': 'lyrics'},
    },
}


class OriginsTests(unittest.TestCase):
    def test_an_apps_origins_extend_connect_and_image_sources(self):
        self.assertEqual(oc.policy(oc.check_origins(copy.deepcopy(ORIGINS))),
                         "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://musicbrainz.org "
                         "https://coverartarchive.org https://*.archive.org https://lrclib.net; "
                         "img-src 'self' https://coverartarchive.org https://*.archive.org; frame-ancestors 'none'")
        self.assertEqual(oc.policy(dict(ORIGINS, origins={})),
                         "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")

    def test_the_file_is_the_apps_own_and_may_carry_an_images_provenance(self):
        own = json.loads(oc.origins_data(ORIGINS))
        self.assertEqual(set(own), {'schema_version', 'api', 'description', 'origins'})
        profile = firmware_profile.load_profile()
        bound = json.loads(oc.origins_data(ORIGINS, profile))
        self.assertEqual((bound['profile_sha256'], bound['origins_sha256']), (firmware_profile.fingerprint(profile), oc.fingerprint(ORIGINS)))
        # Read back from a file, both pass; a file the service would not read does not.
        with tempfile.TemporaryDirectory() as d:
            for data in (oc.origins_data(ORIGINS), oc.origins_data(ORIGINS, profile)):
                (Path(d)/'origins.json').write_bytes(data)
                self.assertEqual(oc.load_origins(Path(d)/'origins.json')['origins'], ORIGINS['origins'])
            (Path(d)/'origins.json').write_bytes(b'{' + b' ' * oc.MAX_BYTES + b'}')
            with self.assertRaisesRegex(ValueError, 'not an origins.json'):
                oc.load_origins(Path(d)/'origins.json')

    def test_origins_are_checked_character_by_character(self):
        for good in ('https://musicbrainz.org', 'https://*.archive.org', 'https://a-b.example.org:8443'):
            self.assertTrue(oc.valid_origin(good), good)
        for bad in ('http://musicbrainz.org', 'https://MusicBrainz.org', 'https://*.org', 'https://org', 'https://a.org/',
                    'https://a.org/path', 'https://a.org:0', 'https://a.org:70000', 'https://a.org; script-src *',
                    "https://a.org 'unsafe-inline'", 'https://-a.org', 'https://a-.org', 'https://a..org', 'https://*',
                    '*', 'https://a.org\r\nX: y', 'https://' + 'a' * 64 + '.org', 'https://a.*.org', 'data:', 'blob:'):
            self.assertFalse(oc.valid_origin(bad), bad)

    def test_tampered_files_are_rejected(self):
        def rejected(mutate, message):
            broken = copy.deepcopy(ORIGINS); mutate(broken)
            with self.assertRaisesRegex(ValueError, message):
                oc.check_origins(broken)
        first = lambda c: c['origins']['musicbrainz']
        rejected(lambda c: first(c).__setitem__('origin', 'https://a.org; script-src *'), 'https origin')
        rejected(lambda c: first(c).__setitem__('directives', ['script-src']), 'directives')
        rejected(lambda c: first(c).__setitem__('directives', []), 'directives')
        rejected(lambda c: first(c).__setitem__('origin', 'https://lrclib.net'), 'duplicate')
        rejected(lambda c: first(c).__setitem__('note', 'x'), 'unexpected fields')
        rejected(lambda c: c['origins'].__setitem__('Bad', dict(first(c))), 'invalid name')
        rejected(lambda c: c.__setitem__('schema_version', 2), 'schema_version 1')
        rejected(lambda c: c.__setitem__('extra', 1), 'optionally')
        rejected(lambda c: c.pop('origins'), 'optionally')
        rejected(lambda c: c['origins'].update({f'extra{n}': {'origin': f'https://e{n}.org', 'directives': ['img-src'],
                                                               'purpose': 'x'} for n in range(oc.MAX_ORIGINS)}), 'At most 32')


if __name__ == '__main__':
    unittest.main()
