"""scripts/release.py with the package builder replaced by a stand-in (the boot layer's
package.py is not needed here): release versions only, the record kept once, and the release
workflow's check refusing a build or a list of files that is not the accepted one."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import release  # noqa: E402


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mips = self.root/'mips'
        self.mips.mkdir()
        (self.mips/'disc-service-release').write_bytes(b'\x7fELF gateway')
        (self.mips/'build-id').write_text('181c5a8d0000\n')
        self.calls = []

    def packer(self, binary, output, version, profile_version):
        self.calls.append((binary.name, version, profile_version))
        output.mkdir(parents=True)
        archive = output/f'disc-server-{version}.zip'
        archive.write_bytes(b'PK' + binary.read_bytes() + version.encode())
        return dict(zip=str(archive))

    def build(self, folder='dist', version='2.57.1', **kw):
        return release.build(version, self.root/folder, self.mips, packer=self.packer, **kw)

    def test_a_release_is_the_release_variant_for_its_firmware(self):
        built = self.build()
        self.assertEqual(self.calls, [('disc-service-release', '2.57.1', '2.57')])
        self.assertEqual(sorted(p.name for p in (self.root/'dist').iterdir()), ['SHA256SUMS', 'disc-server-2.57.1.zip'])
        self.assertEqual(built['buildId'], '181c5a8d0000')
        for version, message in {'2.57': 'not a release version', '2.57.1-debug': 'not a release version',
                                 '3.01.1': 'not a reviewed firmware profile'}.items():
            with self.subTest(version), self.assertRaisesRegex(release.ReleaseError, message):
                self.build('x', version=version)
        (self.mips/'build-id').write_text('181c5a8d0000+changes\n')
        with self.assertRaisesRegex(release.ReleaseError, 'build committed sources'):
            self.build('y')

    def test_a_recorded_build_passes_its_check_and_others_do_not(self):
        self.build()
        releases = self.root/'releases'
        with self.assertRaisesRegex(release.ReleaseError, 'no record'):
            release.check('2.57.1', self.root/'dist', '181c5a8d0000', releases)
        data = release.record('2.57.1', self.root/'dist', 'two_packages.py with boot 2.57.1', '181c5a8d0000', releases)
        self.assertEqual(data['urls']['disc-server-2.57.1.zip'],
                         'https://github.com/eudj1n/snowsky-disc-server/releases/download/v2.57.1/disc-server-2.57.1.zip')
        with self.assertRaisesRegex(release.ReleaseError, 'recorded once'):
            release.record('2.57.1', self.root/'dist', 'again', '181c5a8d0000', releases)
        changelog = self.root/'CHANGELOG.md'
        changelog.write_text('# Changelog\n\n## [2.57.2] — unreleased\n\n- Next.\n\n## [2.57.1] — 2026-10-03\n\n- First.\n')
        text = release.notes(release.check('2.57.1', self.root/'dist', '181c5a8d0000', releases), changelog)
        self.assertTrue(text.startswith('- First.\n\n---\n'), text)
        self.assertIn('`disc-server-2.57.1.zip`', text)
        with self.assertRaisesRegex(release.ReleaseError, 'build id differ'):
            release.check('2.57.1', self.root/'dist', 'ffffffffffff', releases)
        (self.mips/'disc-service-release').write_bytes(b'\x7fELF another gateway')
        self.build('other')
        with self.assertRaisesRegex(release.ReleaseError, 'disc-server-2.57.1.zip differ'):
            release.check('2.57.1', self.root/'other', '181c5a8d0000', releases)

    def test_the_notes_begin_with_the_changelog_and_need_its_dated_section(self):
        changelog = self.root/'CHANGELOG.md'
        changelog.write_text('# Changelog\n\n## [2.57.3] — unreleased\n\n- Next.\n\n'
                             '## [2.57.2] — 2026-10-06\n\nWhat changes.\n\n### Apps\n\n- One.\n\n## [2.57.1] — 2026-10-03\n\n- First.\n')
        self.assertEqual(release.changes('2.57.2', changelog), 'What changes.\n\n### Apps\n\n- One.')
        self.assertEqual(release.changes('2.57.1', changelog), '- First.')
        with self.assertRaisesRegex(release.ReleaseError, 'no release date'):
            release.changes('2.57.3', changelog)
        with self.assertRaisesRegex(release.ReleaseError, 'no section for 2.57.9'):
            release.changes('2.57.9', changelog)

    def test_the_repository_records_are_releases(self):
        for path in sorted((ROOT/'releases').glob('*.json')) if (ROOT/'releases').is_dir() else []:
            with self.subTest(path.name):
                data = json.loads(path.read_text())
                self.assertEqual(release.firmware_of(data['version']), data['firmware'])
                self.assertEqual((path.stem, data['tag']), (data['version'], f'v{data["version"]}'))


if __name__ == '__main__':
    unittest.main()
