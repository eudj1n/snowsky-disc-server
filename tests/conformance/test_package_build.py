"""The gateway as the boot layer's service package (scripts/build_package.py).

Needs a snowsky-disc-boot checkout (DISC_BOOT_DIR, by default the sibling) for
its package tool and, for the end-to-end case, its host fixture build of
disc-boot; without them the cases are skipped.
"""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
BOOT = Path(os.environ.get('DISC_BOOT_DIR', ROOT.parent/'snowsky-disc-boot'))
HAVE_BOOT = (BOOT/'scripts/package.py').is_file()
BOOT_FIXTURE = BOOT/'build/host/disc-boot-fixture'
SERVICE = ROOT/'build/host/disc-service'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def soft_float_elf(path, fp_abi=3):
    """A minimal static MIPS ELF with MIPS ABI flags, enough for the boot builder's check."""
    data = bytearray(256)
    data[:7] = b'\x7fELF\x01\x01\x01'
    struct.pack_into('<HHIIIIIHHH', data, 16, 2, 8, 1, 0, 52, 0, 0x1007, 52, 32, 2)
    struct.pack_into('<8I', data, 52, 1, 160, 0, 0, 16, 16, 5, 4)
    struct.pack_into('<8I', data, 84, 0x70000003, 200, 0, 0, 24, 24, 4, 8)
    struct.pack_into('<HBBBBBBIIII', data, 200, 0, 1, 0, 32, 32, 0, fp_abi, 0, 0, 0, 0)
    path.write_bytes(data)
    return path


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@unittest.skipUnless(HAVE_BOOT, 'snowsky-disc-boot is not checked out beside this repository (DISC_BOOT_DIR)')
class PackageBuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.builder = load('server_package_builder', ROOT/'scripts/build_package.py')
        self.package = load('boot_package_tool', BOOT/'scripts/package.py')

    def options(self, script):
        return [line.split('"')[1] for line in script.splitlines() if line.strip().startswith('"--')]

    def test_the_package_holds_the_gateway_its_start_script_and_catalogs(self):
        result = self.builder.build(soft_float_elf(self.root/'disc-service'), self.root/'out', version='2026.10.02-test')
        folder = Path(result['folder'])
        manifest = json.loads((folder/'package.json').read_text())
        self.assertEqual((manifest['name'], manifest['version'], manifest['role'], manifest['entry'], manifest['arch']),
                         ('disc-server', '2026.10.02-test', 'service', 'bin/run', 'mips32el-linux-static'))
        self.assertEqual(sorted(manifest['files']), ['bin/disc-service', 'bin/run', 'catalog/commands.json', 'catalog/compatibility.json',
                                                     'catalog/hosted.json', 'catalog/queries.json', 'catalog/store.json'])
        self.package.check(Path(result['zip']).parent/'disc-server', 'service', '2.57')
        with tempfile.TemporaryDirectory() as temp:
            self.package.check(self.package.source_folder(result['zip'], temp), 'service', '2.57')
        script = (folder/'bin/run').read_text()
        self.assertTrue(script.startswith('#!/bin/sh\n'))
        self.assertIn('exec "$DISC_BOOT_SLOT/bin/disc-service"', script)
        options = self.options(script)
        self.assertEqual(options, ['--listen', '--upstream', '--settings', '--ready-file', '--boot-status', '--apps', '--sd-mount',
                                   '--sd-source', '--commands-profile-sha256', '--catalog', '--card-catalog', '--data-root',
                                   '--current-lyrics', '--serial-file', '--battery-dir', '--asound-dir', '--player-process',
                                   '--mdns-name', '--database', '--trash', '--internal-lists', '--external-lists'])
        # The boot layer supervises and decides: no supervisor, identity file or card switch of the images.
        for gone in ('--supervise', '--restart-log', '--image-info', '--image-app', '--disable-switch', '--raw-marker', '--card-commands'):
            self.assertNotIn(gone, script)
        self.assertIn('"--catalog" "$DISC_BOOT_SLOT/catalog"', script)
        self.assertIn('"--settings" "$DISC_BOOT_DATA/server.env"', script)
        self.assertNotIn('--port', script, 'the ports come from the settings file')
        self.assertIn('"--database" "$DISC_BOOT_CARD/.disc/disc.db"', script)
        self.assertEqual(subprocess.run(['sh', '-n', str(folder/'bin/run')]).returncode, 0)

    def test_the_engineering_variant_adds_the_cards_commands_and_raw_mode(self):
        result = self.builder.build(soft_float_elf(self.root/'disc-service'), self.root/'out', version='1', engineering=True)
        script = (Path(result['folder'])/'bin/run').read_text()
        self.assertEqual(result['version'], '1-engineering')
        self.assertIn('"--card-commands" "$DISC_BOOT_CARD/.disc/catalog"', script)
        self.assertIn('"--raw-marker" "$DISC_BOOT_CARD/.disc/dev/raw-records"', script)

    def test_only_a_soft_float_mips_gateway_is_packaged(self):
        with self.assertRaisesRegex(ValueError, 'soft-float'):
            self.builder.build(soft_float_elf(self.root/'hard', fp_abi=1), self.root/'out')
        self.assertFalse((self.root/'out').exists())
        self.builder.build(soft_float_elf(self.root/'disc-service'), self.root/'out', version='1')
        with self.assertRaisesRegex(ValueError, 'exists'):
            self.builder.build(soft_float_elf(self.root/'disc-service'), self.root/'out', version='2')

    @unittest.skipUnless(BOOT_FIXTURE.is_file() and SERVICE.is_file(), 'needs both host builds (scripts/test.sh in each repository)')
    def test_disc_boot_installs_and_confirms_the_gateway_which_reports_it(self):
        port, manager_port = free_port(), free_port()
        result = self.builder.build(SERVICE, self.root/'out', version='7', arch='fixture', port=port, manager_port=manager_port)
        player = self.root/'player'
        for name in ('run', 'usr/data', 'tmp/sdcard', 'proc', 'fixture'):
            (player/name).mkdir(parents=True)
        (player/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n')
        (player/'fixture/keys').write_text('play')
        self.package.stage(result['zip'], player/'tmp/sdcard', profile='2.57', arch='fixture')
        env = dict(os.environ, DISC_BOOT_FIXTURE_ROOT=str(player), DISC_BOOT_FIXTURE_TIMING='confirm=1,grace=2,card=2')
        boot = lambda *a: subprocess.run([str(BOOT_FIXTURE), *a], env=env, capture_output=True, text=True, timeout=30)
        try:
            boot('early', '--profile', '2.57', '--card', '/tmp/sdcard', '--card-source', '/dev/mmcblk0p1')
            boot('start')
            status, until = None, time.monotonic() + 20
            while time.monotonic() < until:
                path = player/'run/disc-boot/service.json'
                status = json.loads(path.read_text()) if path.exists() else None
                if status and status['state'] == 'confirmed':
                    break
                time.sleep(0.1)
            log = player/'run/disc-boot/service/log'
            self.assertEqual((status or {}).get('state'), 'confirmed', f'{status}; {log.read_text() if log.exists() else ""}')
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            connection.request('GET', '/api/about', headers={'Host': f'127.0.0.1:{port}'})
            about = json.loads(connection.getresponse().read())
            self.assertEqual((about['boot']['decision']['reason'], about['boot']['service']['name'],
                              about['boot']['service']['version'], about['boot']['service']['state']),
                             ('recovery', 'disc-server', '7', 'confirmed'))
            self.assertEqual(json.loads((player/'tmp/sdcard/.disc/boot/result.json').read_text())['roles']['service']['installed'], True)
            self.assertEqual(about['ports'], {'apps': port, 'manager': manager_port})
            manager = http.client.HTTPConnection('127.0.0.1', manager_port, timeout=5)
            manager.request('GET', '/', headers={'Host': f'127.0.0.1:{manager_port}'})
            self.assertEqual(manager.getresponse().status, 200, 'the package serves its manager')
        finally:
            boot('stop')
            subprocess.run(['pkill', '-f', str(player)], capture_output=True)


if __name__ == '__main__':
    unittest.main()
