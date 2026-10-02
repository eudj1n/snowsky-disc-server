#!/usr/bin/env python3
"""The application manager's installation from a zip (device/src/apps.c), rule by
rule through build/host/app-install-tool, and its parity with the rules of
scripts/app_bundle.py: what the manager refuses, the packing tool refuses too,
with the same words. The HTTP routes are in test_gateway.py.
DISC_APP_TOOL_COMMAND (a JSON list) runs another build, such as the MIPS one
under qemu-user."""
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TOOL = json.loads(os.environ.get('DISC_APP_TOOL_COMMAND', json.dumps([str(ROOT/'build/host/app-install-tool')])))
spec = importlib.util.spec_from_file_location('app_bundle', ROOT/'scripts/app_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

INDEX = b'<!doctype html><link rel="stylesheet" href="./app.css"><script type="module" src="./app.js"></script>'
SCRIPT = b'fetch("/api/health").then(r => r.json())\n'
STYLE = b'body{background:url(./bg.png)}'
DEFLATED, STORED = zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED


def entries(files, folder='Radio'):
    return [(f'{folder}/{name}', data) for name, data in files.items()]


def good(**more):
    return {'index.html': INDEX, 'app.js': SCRIPT, 'app.css': STYLE, **more}


def zip_bytes(items, stream=False, zip64_headers=False):
    """A zip of (name, data[, options]) entries; options: method, attr (st_mode), system."""
    class Unseekable:
        def __init__(self): self.buffer = io.BytesIO()
        def write(self, data): return self.buffer.write(data)
        def flush(self): pass
    out = Unseekable() if stream else io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for item in items:
            name, data, options = item if len(item) == 3 else (*item, {})
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = options.get('method', DEFLATED)
            info.create_system = options.get('system', 3)
            info.external_attr = (0o40755 << 16 | 0x10) if name.endswith('/') else options.get('attr', 0o100644) << 16
            with z.open(info, 'w', force_zip64=zip64_headers) as target:
                target.write(data)
    return (out.buffer if stream else out).getvalue()


def central(data, name):
    """The offset of an entry's central directory record."""
    at = data.find(b'PK\x01\x02')
    while at >= 0:
        length = struct.unpack_from('<H', data, at + 28)[0]
        if data[at + 46:at + 46 + length] == name.encode():
            return at
        at = data.find(b'PK\x01\x02', at + 46 + length)
    raise KeyError(name)


def patch(data, at, fmt, value):
    data = bytearray(data)
    struct.pack_into(fmt, data, at, value)
    return bytes(data)


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root/'card'/'Apps'
        self.apps.parent.mkdir()
        self.count = 0

    def tool(self, *args):
        done = subprocess.run([*TOOL, *map(str, args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.rstrip('\n').split('\t')

    def install(self, data):
        self.count += 1
        archive = self.root/f'upload-{self.count}.zip'
        archive.write_bytes(data)
        return self.tool('install', self.apps, archive)

    def refused(self, data):
        result = self.install(data)
        self.assertEqual(result[0], '-1', result)
        return result[1]

    def python(self, data):
        """app_bundle.py's verdict on a zip: its reading, then its checks of the files as a folder."""
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp)/'app.zip'; archive.write_bytes(data)
            try:
                _, files = bundle.read_zip(archive)
                folder = Path(temp)/'app'; folder.mkdir()
                for name, content in files.items():
                    (folder/name).parent.mkdir(parents=True, exist_ok=True)
                    (folder/name).write_bytes(content)
                bundle.app_files(folder)
                return 'ok'
            except ValueError as refusal:
                return str(refusal)

    def leftovers(self):
        return sorted(p.name for p in self.apps.iterdir() if p.name.startswith('.')) if self.apps.exists() else []

    def test_installs_an_app_and_skips_what_is_never_served(self):
        twin = bundle.gzip_twin(b'a' * 4096)
        data = zip_bytes([('Radio/', b''), ('Radio/assets/', b''), *entries(good(**{
            'assets/big.txt': b'a' * 4096, 'assets/big.txt.gz': twin, 'assets/logo.png': b'\x89PNG', 'app.json': b'{"version":"1.0.0"}'})),
            ('Radio/.DS_Store', b'x'), ('Radio/.git/config', b'x'), ('__MACOSX/Radio/._index.html', b'x'),
            ('Radio/stored.txt', b'kept as is', {'method': STORED})])
        self.assertEqual(self.install(data), ['ok', 'Radio', '8', str(len(INDEX) + len(SCRIPT) + len(STYLE) + 4096 + len(twin) + 4 + 19 + 10)])
        self.assertEqual(sorted(p.relative_to(self.apps/'Radio').as_posix() for p in (self.apps/'Radio').rglob('*') if p.is_file()),
                         ['app.css', 'app.js', 'app.json', 'assets/big.txt', 'assets/big.txt.gz', 'assets/logo.png', 'index.html', 'stored.txt'])
        self.assertEqual((self.apps/'Radio'/'assets'/'big.txt.gz').read_bytes(), twin)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.python(data), 'ok')

    def test_reads_the_zips_tools_write(self):
        # Streamed (sizes in data descriptors, as Finder's) and with zip64 extras in the local headers.
        for options in ({'stream': True}, {'zip64_headers': True}):
            with self.subTest(**options):
                data = zip_bytes(entries(good()), **options)
                self.assertEqual(self.install(data)[:3], ['ok', 'Radio', '3'])
                self.assertEqual((self.apps/'Radio'/'app.js').read_bytes(), SCRIPT)
        # A UTF-8 app name, as the packing tool allows.
        self.assertEqual(self.install(zip_bytes(entries(good(), 'Радио')))[:2], ['ok', 'Радио'])

    def test_a_new_version_replaces_the_app_whole(self):
        self.install(zip_bytes(entries(good(**{'old.js': b'1'}))))
        (self.apps/'.Radio.installing').mkdir(); (self.apps/'.Radio.previous').mkdir()  # an interrupted earlier one
        self.assertEqual(self.install(zip_bytes(entries(good(**{'new.js': b'2'}))))[0], 'ok')
        self.assertTrue((self.apps/'Radio'/'new.js').exists())
        self.assertFalse((self.apps/'Radio'/'old.js').exists())
        self.assertEqual(self.leftovers(), [])

    def test_a_refused_or_damaged_update_keeps_the_installed_app(self):
        self.install(zip_bytes(entries(good(**{'old.js': b'1'}))))
        data = zip_bytes(entries(good(**{'z.js': b'late entry'})))
        damaged = patch(data, central(data, 'Radio/z.js') + 16, '<I', 0)
        self.assertEqual(self.refused(damaged), 'z.js does not match its size or CRC')
        self.assertEqual(self.refused(zip_bytes(entries(good(**{'index.html': b'<script>x()</script>'})))),
                         'index.html: inline script is blocked by the gateway CSP; move it into a file')
        self.assertTrue((self.apps/'Radio'/'old.js').exists())
        self.assertEqual(self.leftovers(), [])

    def test_refuses_archives_it_cannot_trust(self):
        data = zip_bytes(entries(good()))
        index = central(data, 'Radio/index.html')
        local = struct.unpack_from('<I', data, index + 42)[0]
        start = local + 30 + sum(struct.unpack_from('<HH', data, local + 26))
        eocd = data.rfind(b'PK\x05\x06')
        cases = {
            'not a zip': (b'<html>' * 10, 'Not a zip archive'),
            'truncated': (data[:-30], 'Not a zip archive'),
            'zip64 or split': (patch(patch(data, eocd + 8, '<H', 0xffff), eocd + 10, '<H', 0xffff), 'Split or zip64 archives are not supported'),
            'encrypted': (patch(data, index + 8, '<H', 1), 'index.html: encrypted or an unsupported compression'),
            'bzip2': (zip_bytes(entries(good()) + [('Radio/b.txt', b'b', {'method': zipfile.ZIP_BZIP2})]), 'b.txt: encrypted or an unsupported compression'),
            'symlink': (zip_bytes(entries(good()) + [('Radio/link.js', b'/etc/passwd', {'attr': 0o120777})]), 'Unsupported file in the app: link.js'),
            'damaged deflate': (patch(data, start, '<B', 0x07), 'index.html: damaged compressed data'),
            'hidden app name': (zip_bytes(entries(good(), '.Radio')), 'Not an app name: .Radio'),
            'trailing space': (zip_bytes(entries(good(), 'Radio ')), 'Not an app name: Radio '),
            'name not UTF-8': (patch(zip_bytes(entries(good(), 'Радио')), central(zip_bytes(entries(good(), 'Радио')), 'Радио/index.html') + 8, '<H', 0),
                               "An entry's name is not UTF-8"),
        }
        for case, (archive, problem) in cases.items():
            with self.subTest(case):
                self.assertEqual(self.refused(archive), problem)
        big = self.root/'big.zip'
        with open(big, 'wb') as f:
            f.truncate(40 * 1024 * 1024 + 1)
        self.assertEqual(self.tool('install', self.apps, big), ['-1', 'The archive is too large'])
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.apps/'Radio').exists())

    def test_the_rules_match_the_packing_tool(self):
        four = 4 * 1024 * 1024
        cases = {
            'a file outside the folder': ([('index.html', INDEX)], 'Unexpected entry in the zip: index.html'),
            'a parent reference': (entries(good()) + [('Radio/../x.js', b'x')], 'Unexpected entry in the zip: Radio/../x.js'),
            'an absolute name': ([('/Radio/index.html', INDEX)], 'Unexpected entry in the zip: /Radio/index.html'),
            'two apps': (entries(good()) + entries(good(), 'Other'), 'The zip must hold exactly one app folder'),
            'a type never served': (entries(good(**{'run.sh': b'x'})), 'Unsupported file in the app: run.sh'),
            'a space in a name': (entries(good(**{'a b.js': b'x'})), 'Unsupported file in the app: a b.js'),
            'a long name': (entries(good(**{'a' * 78 + '.js': b'x'})), f'Unsupported file in the app: {"a" * 78}.js'),
            'too deep': (entries(good(**{'a/b/c/d/e/f.js': b'x', 'a/b/c/d/e/f/g.js': b'x'})), 'Unsupported file in the app: a/b/c/d/e/f/g.js'),
            'a large file': (entries(good(**{'big.txt': b'\0' * (four + 1)})), 'Unsupported file in the app: big.txt'),
            'too much': (entries(good(**{f'big{n}.txt': b'\0' * four for n in range(8)})), 'The app exceeds the file count or total size limit'),
            'too many files': (entries(good(**{f'f{n}.txt': b'x' for n in range(510)})), 'The app exceeds the file count or total size limit'),
            'a catalog': (entries(good(**{'commands.json': b'{}'})), 'commands.json: the reviewed catalogs come from the service, not from an app'),
            'a twin that is not gzip': (entries(good(**{'app.js.gz': b'plain'})), 'Not a gzip twin: app.js.gz'),
            'a twin of nothing': (entries(good(**{'x.txt.gz': bundle.gzip_twin(b'x' * 4096)})), 'Unsupported file in the app: x.txt.gz'),
            'a twin of an image': (entries(good(**{'a.png': b'p', 'a.png.gz': b'\x1f\x8bx'})), 'Unsupported file in the app: a.png.gz'),
            'no index': (entries({'app.js': SCRIPT}), 'An app needs index.html at its root'),
            'inline script': (entries(good(**{'index.html': b'<script type="module">import "./a.js"</script>'})),
                              'index.html: inline script is blocked by the gateway CSP; move it into a file'),
            'an empty script': (entries(good(**{'index.html': b'<SCRIPT></SCRIPT>'})), 'index.html: inline script is blocked by the gateway CSP; move it into a file'),
            'inline style': (entries(good(**{'index.html': b'<style>\n p{}</style>'})), 'index.html: inline style is blocked by the gateway CSP; move it into a file'),
            'a style attribute': (entries(good(**{'index.html': b"<p STYLE = 'color:red'>x</p>"})), 'index.html: inline style is blocked by the gateway CSP; move it into a file'),
            'a handler': (entries(good(**{'index.html': b'<button\nonClick="go()">x</button>'})),
                          'index.html: inline event handler is blocked by the gateway CSP; move it into a file'),
            'a script URL': (entries(good(**{'page.html': b'<a href="JavaScript:void(0)">x</a>'})),
                             'page.html: javascript: URL is blocked by the gateway CSP; move it into a file'),
            'a root-absolute link': (entries(good(**{'index.html': b'<link HREF = "/app.css" rel=stylesheet>'})),
                                     'index.html: a root-absolute href or src resolves only at /; use relative references (Vite: base "./")'),
            'a root-absolute import': (entries(good(**{'app.js': b'import("/assets/chunk.js")'})),
                                       'app.js: root-absolute asset reference /assets/chunk.js resolves only at /; use relative references'),
            'a root-absolute font': (entries(good(**{'app.css': b'@font-face{src:url(/fonts/a.woff2)}'})),
                                     'app.css: root-absolute asset reference /fonts/a.woff2 resolves only at /; use relative references'),
            'a type in an earlier segment': (entries(good(**{'app.js': b'x="/a.js/b"'})),
                                             'app.js: root-absolute asset reference /a.js resolves only at /; use relative references'),
            'an upper-case API path': (entries(good(**{'app.mjs': b"x='/API/x.json'"})),
                                       'app.mjs: root-absolute asset reference /API/x.json resolves only at /; use relative references'),
            'an upper-case page': (entries(good(**{'PAGE.HTML': b'<p onload="x">'})),
                                   'PAGE.HTML: inline event handler is blocked by the gateway CSP; move it into a file'),
        }
        for case, (items, problem) in cases.items():
            with self.subTest(case):
                data = zip_bytes(items)
                self.assertEqual(self.refused(data), problem, 'the manager')
                self.assertEqual(self.python(data), problem, 'the packing tool')
        # What both accept: references the gateway resolves wherever the app is served.
        fine = {'index.html': b'<a href="./x.html" data-x="/x">on-line</a><img src="//cdn.example/a.png"><script src="./app.js"></script>'
                              b'<link href="/api/x">',
                'app.js': b'fetch("/api/health"); x="//cdn.example/a.js"; y="/a.jsx"; z="/.js"; w="/a//b.js"',
                'app.css': STYLE, 'x.html': b'<p>x</p>', 'a.JS': b'1', 'A.txt': b'1', 'f.webmanifest': b'{}'}
        data = zip_bytes(entries(fine))
        self.assertEqual(self.python(data), 'ok')
        self.assertEqual(self.install(data)[:3], ['ok', 'Radio', '7'])

    def test_a_case_collision_is_refused(self):
        # Not compared with the packing tool: it reads folders, and a case-insensitive disk keeps one of the two.
        self.assertEqual(self.refused(zip_bytes(entries(good(**{'App.js': b'1', 'app.js': b'2'})))),
                         'Case-insensitive name collision on a FAT card: App.js')

    def test_removes_an_app(self):
        self.install(zip_bytes(entries(good())))
        (self.apps/'Notes').mkdir(); (self.apps/'Notes'/'readme.txt').write_text('not an app')
        self.assertEqual(self.tool('remove', self.apps, 'Radio'), ['ok'])
        self.assertFalse((self.apps/'Radio').exists())
        for name in ('Radio', 'Notes', '../card', '.Radio.previous', ''):
            with self.subTest(name=name):
                self.assertEqual(self.tool('remove', self.apps, name), ['-1', 'No such app'])
        self.assertTrue((self.apps/'Notes'/'readme.txt').exists())
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
