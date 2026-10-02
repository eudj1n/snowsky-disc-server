"""Firmware-free HTTP and bundle conformance for the native SD webroot."""
import gzip
import http.client
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from test_service import Peer, TCP, WS

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('app_bundle', ROOT/'scripts/app_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class WebrootTests(unittest.TestCase):
    """Apps as plain folders (combined-009): <card>/Apps/<App>/ served as it is copied."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root/'Apps'
        self.app = self.apps/'Disc Player'
        self.app.mkdir(parents=True)
        (self.app/'index.html').write_text('<link href="./style.css"><script src="./app.js"></script>')
        (self.app/'style.css').write_text('body{color:blue}')
        (self.app/'app.js').write_text('console.log("version one")')
        self.proc = None
        self.listen = port()
        self.authority = f'127.0.0.1:{self.listen}'
        self.start()

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    def start(self, *extra):
        self.proc = subprocess.Popen([
            str(ROOT/'build/host/disc-service'), '--port', str(self.listen),
            '--authority', self.authority, '--apps', str(self.apps), *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if self.fetch('/api/health')[0] == 200:
                    return
            except OSError:
                time.sleep(.02)
        raise RuntimeError('Native test service failed to start')

    def fetch(self, path, method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.listen, timeout=5)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def test_copying_new_files_updates_the_app_without_a_restart(self):
        status, index, headers = self.fetch('/')
        self.assertEqual((status, headers['Cache-Control']), (200, 'no-store'))
        self.assertIn(b'./style.css', index)
        self.assertEqual(self.fetch('/app.js')[1], b'console.log("version one")')
        self.assertEqual(self.fetch('/app.js', 'HEAD')[1], b'')
        self.assertEqual(self.fetch('/', 'HEAD')[1], b'')
        (self.app/'app.js').write_text('console.log("version two")')
        self.assertEqual(self.fetch('/app.js')[1], b'console.log("version two")')
        self.assertEqual(self.fetch('/app.js')[2]['Cache-Control'], 'no-cache')

    def test_text_files_are_served_from_their_gzip_twins_to_browsers_that_accept_them(self):
        script = 'const line = "the same line of a large app script";\n' * 2000
        (self.app/'app.js').write_text(script)
        (self.app/'app.js.gz').write_bytes(bundle.gzip_twin(script.encode()))
        packed = (self.app/'app.js.gz').read_bytes()
        for accept, encoded in [('gzip, deflate, br, zstd', True), ('GZIP;q=0.5', True),
                                ('br, gzip;q=0', False), ('gzip;q=0.000', False), ('identity', False), (None, False)]:
            with self.subTest(accept=accept):
                status, body, headers = self.fetch('/app.js', headers={'Accept-Encoding': accept} if accept else {})
                self.assertEqual(status, 200)
                self.assertEqual(headers['Vary'], 'Accept-Encoding')
                self.assertEqual(headers['Content-Type'], 'text/javascript; charset=utf-8')
                self.assertEqual(headers.get('Content-Encoding'), 'gzip' if encoded else None)
                self.assertEqual(body, packed if encoded else script.encode())
        status, body, headers = self.fetch('/app.js', 'HEAD', {'Accept-Encoding': 'gzip'})
        self.assertEqual((body, headers['Content-Length'], headers['Content-Encoding']), (b'', str(len(packed)), 'gzip'))
        # The twin is only an encoding: never addressed itself, never served without its original.
        self.assertEqual(self.fetch('/app.js.gz', headers={'Accept-Encoding': 'gzip'})[0], 404)
        (self.app/'app.js').unlink()
        self.assertEqual(self.fetch('/app.js', headers={'Accept-Encoding': 'gzip'})[0], 404)

    def test_without_the_apps_folder_or_its_index_the_embedded_page_answers(self):
        original = self.fetch('/')[1]
        self.apps.rename(self.root/'removed')
        self.assertIn(b'NATIVE PROBE', self.fetch('/')[1])
        (self.root/'removed').rename(self.apps)
        self.assertEqual(self.fetch('/')[1], original)
        (self.app/'index.html').unlink()
        self.assertIn(b'NATIVE PROBE', self.fetch('/')[1])

    def test_query_strings_are_ignored_on_documents_and_refused_on_api_routes(self):
        status, body, headers = self.fetch('/?view=album&name=CI%20Album')
        self.assertEqual((status, headers['Cache-Control']), (200, 'no-store'))
        self.assertIn(b'./app.js', body)
        self.assertEqual(self.fetch('/app.js?v=1')[0], 200)
        self.assertEqual(self.fetch('/api/health?x=1')[0], 405)
        self.assertEqual(self.fetch('/api/catalog/stream?start-pos=0')[0], 405)

    def test_confinement_methods_and_api_priority(self):
        (self.app/'secret.js').symlink_to(self.root/'outside.js')
        (self.root/'outside.js').write_text('secret')
        (self.app/'large.js').write_bytes(b'x'*(4*1024*1024+1))
        (self.app/'unknown.exe').write_text('binary')
        for path in ('/secret.js', '/large.js', '/unknown.exe', '/../outside.js', '/%2e%2e/outside.js',
                     '//app.js', '/bad\\name.js', '/apps/Disc%20Player/../../outside.js', '/api/unknown'):
            self.assertNotEqual(self.fetch(path)[0], 200, path)
        self.assertEqual(self.fetch('/api/health')[0], 200)
        self.assertEqual(self.fetch('/api/health', 'HEAD')[0], 405)
        self.assertEqual(self.fetch('/app.js', 'POST')[0], 405)
        self.assertEqual(self.fetch('/app.js', headers={'Origin': 'https://other.invalid'})[0], 403)

    def test_mount_guard_refuses_unowned_card(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.start('--sd-mount', str(self.root), '--sd-source', '/dev/mmcblk0p1')
        self.assertIn(b'NATIVE PROBE', self.fetch('/')[1])
        # The embedded page's own script, never the app's from a card the player does not own.
        self.assertNotEqual(self.fetch('/app.js')[1], b'console.log("version one")')

    def test_the_lan_listener_is_on_by_default_and_checks_host_and_origin(self):
        # combined-008: no marker; a LAN Host is an address or an mDNS name with the service port.
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.start('--authority', f'localhost:{self.listen}')
        self.assertEqual(self.fetch('/')[0], 200)
        self.assertEqual(self.fetch('/', headers={'Origin': f'http://127.0.0.1:{self.listen}'})[0], 200)
        self.assertEqual(self.fetch('/', headers={'Origin': 'http://foreign.invalid'})[0], 403)
        # A rebinding name reaches the address but not the service.
        self.assertEqual(self.fetch('/', headers={'Host': f'attacker.example:{self.listen}'})[0], 403)
        self.assertEqual(self.fetch('/', headers={'Host': '127.0.0.1:1'})[0], 403)

    def test_the_lan_listener_admits_only_the_players_own_mdns_name(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.start('--authority', f'localhost:{self.listen}', '--mdns-name', 'ingenic')
        host = f'ingenic.local:{self.listen}'
        self.assertEqual(self.fetch('/', headers={'Host': host})[0], 200)
        self.assertEqual(self.fetch('/', headers={'Host': host, 'Origin': f'http://{host}'})[0], 200)
        self.assertEqual(self.fetch('/', headers={'Host': f'INGENIC.local:{self.listen}'})[0], 200)
        self.assertEqual(self.fetch('/', headers={'Host': host, 'Origin': f'http://other.local:{self.listen}'})[0], 403)
        # Another .local name could be spoofed on the LAN to rebind a page onto the player: refused.
        for bad in ('other.local', 'a.b.local', '-x.local', 'x-.local', '.local', 'in_genic.local', 'ingenic.local.example',
                    'ingenicx.local', 'xingenic.local'):
            self.assertEqual(self.fetch('/', headers={'Host': f'{bad}:{self.listen}'})[0], 403, bad)
        self.assertEqual(self.fetch('/', headers={'Host': 'ingenic.local:1'})[0], 403)
        # Without a configured name no .local name is admitted.
        self.proc.terminate(); self.proc.wait(timeout=5)
        self.start('--authority', f'localhost:{self.listen}')
        self.assertEqual(self.fetch('/', headers={'Host': host})[0], 403)

    def test_the_app_checker_takes_what_an_app_may_hold(self):
        files = bundle.app_files(self.app)
        self.assertEqual(sorted(files), ['app.js', 'index.html', 'style.css'])
        (self.app/'._app.js').write_text('macOS')  # hidden names are skipped, never packed or served
        self.assertEqual(sorted(bundle.app_files(self.app)), ['app.js', 'index.html', 'style.css'])
        for name, data, why in [('link.js', None, 'Unsupported'), ('tool.exe', b'x', 'Unsupported'),
                                ('commands.json', b'{}', 'catalogs come from the service'),
                                ('inline.html', b'<script>alert(1)</script>', 'inline script'),
                                ('root.html', b'<script src="/app.js"></script>', 'root-absolute'),
                                ('absolute.js', b'import "/assets/x.js"', 'root-absolute')]:
            with self.subTest(name=name):
                path = self.app/name
                if data is None:
                    path.symlink_to(self.app/'app.js')
                else:
                    path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, why):
                    bundle.app_files(self.app)
                path.unlink()
        (self.app/'APP.js').write_text('collision')
        if sum(p.name.casefold() == 'app.js' for p in self.app.iterdir()) == 2:
            with self.assertRaisesRegex(ValueError, 'collision'):
                bundle.app_files(self.app)
