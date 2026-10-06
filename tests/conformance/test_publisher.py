"""Apps and catalogs tooling (scripts/app_bundle.py, combined-009); synthetic folders only."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('app_bundle', ROOT/'scripts/app_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
from scripts import command_catalog, firmware_profile, origins_catalog

VITE_INDEX = ('<!doctype html><html><head><meta charset="utf-8">'
              '<script type="module" crossorigin src="./assets/index-Ab12Cd34.js"></script>'
              '<link rel="modulepreload" crossorigin href="./assets/vendor-Ef56Gh78.js">'
              '<link rel="stylesheet" crossorigin href="./assets/index-Ij90Kl12.css"></head>'
              '<body><div id="app"></div></body></html>')


class AppBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def dist(self, name='dist', js='import("./vendor-Ef56Gh78.js");fetch("/api/health")'):
        source = self.root/name
        (source/'assets').mkdir(parents=True)
        (source/'index.html').write_text(VITE_INDEX)
        (source/'assets'/'index-Ab12Cd34.js').write_text(js)
        (source/'assets'/'vendor-Ef56Gh78.js').write_text('export const v=1;' * 200)
        (source/'assets'/'index-Ij90Kl12.css').write_text('body{margin:0}')
        return source

    def test_app_json_names_the_project_page_only_as_a_plain_https_link(self):
        # The page's pack-app.mjs writes the same bytes (snowsky-disc-player tests/unit/pack-app.test.mjs).
        files = bundle.build_app(self.dist(), 'Disc Player', '2026.10.06', homepage='https://github.com/eudj1n/snowsky-disc-player')
        self.assertEqual(files['app.json'], b'{"schema":1,"name":"Disc Player","version":"2026.10.06",'
                                            b'"homepage":"https://github.com/eudj1n/snowsky-disc-player"}\n')
        self.assertEqual(bundle.build_app(self.dist('dist2'), 'Disc Player', '1')['app.json'],
                         b'{"schema":1,"name":"Disc Player","version":"1"}\n')
        source = self.dist('dist3')
        for bad in ('http://github.com/a', 'javascript:alert(1)', 'https://github.com/a?b=1', 'https://github.com/a#b',
                    'https:///a', 'https://github.com/' + 'a' * 190, 'https://github.com/a"><b'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bundle.build_app(source, 'Disc Player', '1', homepage=bad)

    def test_a_zip_holds_the_app_folder_with_twins_app_json_and_origins(self):
        files = bundle.build_app(self.dist(), 'Disc Player', '2026.09.29', origins_catalog.load_origins())
        report = bundle.zip_app(files, 'Disc Player', self.root/'disc-player.zip')
        with zipfile.ZipFile(self.root/'disc-player.zip') as z:
            names = sorted(z.namelist())
            self.assertTrue(all(name.startswith('Disc Player/') for name in names))
            self.assertEqual(json.loads(z.read('Disc Player/app.json')), {'schema': 1, 'name': 'Disc Player', 'version': '2026.09.29'})
            self.assertEqual(json.loads(z.read('Disc Player/origins.json'))['origins'], json.loads(
                origins_catalog.origins_data(firmware_profile.load_profile(), origins_catalog.load_origins()))['origins'])
            # The large script gets a twin; small files do not; nothing of the catalogs.
            self.assertEqual(gzip.decompress(z.read('Disc Player/assets/vendor-Ef56Gh78.js.gz')), b'export const v=1;' * 200)
            self.assertNotIn('Disc Player/assets/index-Ij90Kl12.css.gz', names)
            self.assertFalse(any(name.endswith(bundle.CATALOGS) for name in names))
        # The same build makes the same zip.
        again = bundle.zip_app(bundle.build_app(self.dist('dist2'), 'Disc Player', '2026.09.29', origins_catalog.load_origins()),
                               'Disc Player', self.root/'again.zip')
        self.assertEqual(report['sha256'], again['sha256'])

    def test_install_replaces_the_app_folder_whole_and_keeps_the_card_on_failure(self):
        card = self.root/'card'
        card.mkdir()
        bundle.zip_app(bundle.build_app(self.dist(), version='1'), 'Disc Player', self.root/'one.zip')
        report = bundle.install(self.root/'one.zip', card)
        app = card/'Apps'/'Disc Player'
        self.assertEqual((report['app'], (app/'index.html').read_text()), ('Disc Player', VITE_INDEX))
        (app/'stale-Zz99Yy88.js').write_text('left from before')
        bundle.zip_app(bundle.build_app(self.dist('two', js='fetch("/api/health") // two'), version='2'), 'Disc Player',
                       self.root/'two.zip')
        bundle.install(self.root/'two.zip', card)
        self.assertFalse((app/'stale-Zz99Yy88.js').exists())
        self.assertEqual(json.loads((app/'app.json').read_text())['version'], '2')
        self.assertEqual(sorted(p.name for p in (card/'Apps').iterdir()), ['Disc Player'])
        # A copy that fails midway leaves the installed app as it was.
        real = bundle.write_new
        def failing(path, data):
            if path.name == 'index.html':
                raise OSError(28, 'No space left on device')
            real(path, data)
        with mock.patch.object(bundle, 'write_new', failing), self.assertRaisesRegex(ValueError, 'keeps what it had'):
            bundle.install(self.root/'one.zip', card)
        self.assertEqual(json.loads((app/'app.json').read_text())['version'], '2')
        self.assertEqual(sorted(p.name for p in (card/'Apps').iterdir()), ['Disc Player'])
        # Not written when the card could not keep room for the service's database.
        real_statvfs = os.statvfs
        def small(path):
            result = real_statvfs(path)
            return os.statvfs_result((4096, 4096, result.f_blocks, 100, 100) + tuple(result)[5:])
        with mock.patch.object(bundle.os, 'statvfs', small), self.assertRaisesRegex(ValueError, 'Nothing was written'):
            bundle.install(self.root/'one.zip', card)

    def test_install_checks_a_zip_like_a_folder(self):
        card = self.root/'card'
        card.mkdir()
        def make(name, entries):
            path = self.root/name
            with zipfile.ZipFile(path, 'w') as z:
                for entry, data in entries.items():
                    z.writestr(entry, data)
            return path
        for name, entries, why in [
            ('two-apps.zip', {'A/index.html': '<p>', 'B/index.html': '<p>'}, 'exactly one app folder'),
            ('no-index.zip', {'App/app.js': 'x'}, 'index.html'),
            ('inline.zip', {'App/index.html': '<script>alert(1)</script>'}, 'inline script'),
            ('catalog.zip', {'App/index.html': '<p>', 'App/commands.json': '{}'}, 'catalogs come from the service'),
            ('escape.zip', {'App/../x/index.html': '<p>'}, 'Unexpected entry'),
            ('hidden.zip', {'.App/index.html': '<p>'}, 'Not an app name')]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, why):
                bundle.install(make(name, entries), card)
        self.assertFalse((card/'Apps').exists() and any((card/'Apps').iterdir()))
        # macOS additions inside a zip are left out, not refused.
        bundle.install(make('mac.zip', {'App/index.html': '<p>', '__MACOSX/App/._index.html': 'x', 'App/.DS_Store': 'x'}), card)
        self.assertEqual(sorted(p.name for p in (card/'Apps'/'App').iterdir()), ['index.html'])

    def test_the_catalogs_are_generated_for_the_image_and_the_cards_override(self):
        files = bundle.catalog_files()
        self.assertEqual(sorted(files), sorted(bundle.CATALOGS))
        profile = firmware_profile.load_profile()
        self.assertEqual(json.loads(files['compatibility.json'])['profile_sha256'], firmware_profile.fingerprint(profile))
        self.assertEqual(json.loads(files['commands.json'])['catalog_sha256'], command_catalog.fingerprint(command_catalog.load_catalog()))
        card = self.root/'card'
        card.mkdir()
        report = bundle.install_catalog(card)
        self.assertEqual(sorted(p.name for p in (card/'.disc'/'catalog').iterdir()), ['hosted.json', 'queries.json', 'store.json'])
        bundle.install_catalog(card, commands=True)
        self.assertEqual(sorted(p.name for p in (card/'.disc'/'catalog').iterdir()),
                         ['commands.json', 'hosted.json', 'queries.json', 'store.json'])
        self.assertEqual((card/'.disc'/'catalog'/'queries.json').read_bytes(), files['queries.json'])
        self.assertEqual(sorted(report['sha256']), ['hosted.json', 'queries.json', 'store.json'])

    def test_the_command_line_needs_confirmation_for_card_writes(self):
        card = self.root/'card'
        card.mkdir()
        command = [sys.executable, str(ROOT/'scripts'/'app_bundle.py')]
        zipped = subprocess.run(command + ['zip', '--source', str(self.dist()), '--output', str(self.root/'a.zip'), '--version', '1',
                                           '--origins'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(zipped.stdout)['app'], 'Disc Player')
        refused = subprocess.run(command + ['install', '--app', str(self.root/'a.zip'), '--card', str(card)],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('--confirm-card-write', refused.stderr)
        self.assertFalse((card/'Apps').exists())
        done = subprocess.run(command + ['install', '--app', str(self.root/'a.zip'), '--card', str(card), '--confirm-card-write'],
                              capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(done.stdout)['path'], str(card/'Apps'/'Disc Player'))
        bad = subprocess.run(command + ['check', '--source', str(self.root/'missing')], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertNotIn('Traceback', bad.stderr)


if __name__ == '__main__':
    unittest.main()
