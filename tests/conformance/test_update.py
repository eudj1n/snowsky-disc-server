#!/usr/bin/env python3
"""The server's updates (device/src/update.c, scripts/update_file.py): the .update
stream rule by rule through build/host/update-tool, the signing tool, and Ed25519
against RFC 8032's vectors. The manager's routes are in test_gateway.py and the
whole path under the boot program in test_package_build.py.
DISC_UPDATE_TOOL_COMMAND (a JSON list) runs another build, such as the MIPS one."""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import ed25519  # noqa: E402
import update_file  # noqa: E402

TOOL = json.loads(os.environ.get('DISC_UPDATE_TOOL_COMMAND', json.dumps([str(ROOT/'build/host/update-tool')])))
SECRET = bytes(range(32))  # a test key, never a release key
OTHER = bytes(range(1, 33))
VECTORS = [  # RFC 8032, 7.1, tests 1-3: secret, public, message, signature
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60', 'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb', '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7', 'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
]
# A stand-in for `disc-boot verify service DIR`: it records the folder and answers as told.
BOOT = '''#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/verify.log"
if [ -e "$(dirname "$0")/refuse" ]; then printf '{"ok":false,"error":"the manifest does not fit"}\\n'; exit 1; fi
printf '{"ok":true,"name":"disc-server","version":"x","bytes":1}\\n'
'''


def manifest(files, name='disc-server', version='2', role='service', modes=None):
    listed = {path: dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=(modes or {}).get(path, '0755' if path.startswith('bin/') else '0644'))
              for path, data in files.items()}
    return json.dumps(dict(schema=1, name=name, version=version, role=role, bootApi=1, arch='fixture', profiles=['2.57'],
                           entry='bin/run', args=[], ready=30, files=listed)).encode()


def stream(manifest_bytes, payload, secret=SECRET):
    return b'DISCUPD1' + ed25519.sign(secret, manifest_bytes) + struct.pack('<I', len(manifest_bytes)) + manifest_bytes + payload


GOOD = {'bin/run': b'#!/bin/sh\nexec true\n', 'catalog/commands.json': b'{}\n'}


class UpdateStreamTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.slot, self.work = self.root/'service/b', self.root/'data/update'
        self.slot.mkdir(parents=True); self.work.parent.mkdir(parents=True)
        (self.slot/'package.json').write_text('{"name":"disc-server","version":"1"}')
        (self.slot/'bin').mkdir(); (self.slot/'bin/run').write_text('old')
        self.keys = self.root/'update-keys'
        self.keys.write_text(f'# test keys\n{ed25519.public_key(SECRET).hex()}\n')
        self.boot = self.root/'boot/disc-boot'
        self.boot.parent.mkdir(); self.boot.write_text(BOOT); self.boot.chmod(0o755)
        self.count = 0

    def stage(self, data, declared=None):
        self.count += 1
        path = self.root/f'{self.count}.update'
        path.write_bytes(data)
        args = ['stage', self.slot, self.work, self.keys, 'disc-server', self.boot, path, *([declared] if declared is not None else [])]
        done = subprocess.run([*TOOL, *map(str, args)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.rstrip('\n').split('\t')

    def untouched(self):
        self.assertEqual((self.slot/'package.json').read_text(), '{"name":"disc-server","version":"1"}')
        self.assertEqual((self.slot/'bin/run').read_text(), 'old')
        self.assertFalse(self.work.exists())

    def test_a_signed_update_takes_the_slot_once_checked(self):
        m = manifest(GOOD)
        self.assertEqual(self.stage(stream(m, b''.join(GOOD.values()))), ['ok', 'disc-server', '2', '2', str(sum(map(len, GOOD.values())))])
        self.assertEqual((self.slot/'package.json').read_bytes(), m)
        for path, data in GOOD.items():
            self.assertEqual((self.slot/path).read_bytes(), data)
        self.assertEqual(stat.S_IMODE((self.slot/'bin/run').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.slot/'catalog/commands.json').stat().st_mode), 0o644)
        self.assertFalse(self.work.exists())
        # Boot checked the update where it was written, before it took the slot.
        self.assertEqual((self.boot.parent/'verify.log').read_text(), f'verify service {self.work}\n')

    def test_refusals_leave_the_slot_as_it_was(self):
        good = manifest(GOOD)
        payload = b''.join(GOOD.values())
        tampered = bytearray(stream(good, payload)); tampered[-1] ^= 1
        twice = good.replace(b'"catalog/commands.json"', b'"bin/run"')
        cases = {
            'a zip': (b'PK\x03\x04' + bytes(100), 'Not a server update (.update) file'),
            'another key': (stream(good, payload, OTHER), 'The update is not signed by a key this server trusts'),
            'a changed manifest': (stream(good, payload)[:80] + b'X' + stream(good, payload)[81:], 'The update is not signed by a key this server trusts'),
            'another package': (stream(manifest(GOOD, name='other-ui'), payload), 'This update is for other-ui, not disc-server'),
            'another role': (stream(manifest(GOOD, role='ui'), payload), 'The update is not a service package'),
            'a mode boot refuses': (stream(manifest(GOOD, modes={'bin/run': '0700'}), payload), 'bin/run: its size, sha256 or mode is missing or invalid'),
            'a path listed twice': (stream(twice, payload), 'bin/run is listed twice'),
            'a changed file': (bytes(tampered), 'catalog/commands.json does not match its sha256'),
            'bytes after the files': (stream(good, payload + b'x'), "The update's length does not match its package.json"),
            'not JSON': (stream(b'{"name":', b''), "The update's package.json is not valid JSON"),
        }
        for path in ('../x', '/etc/x', 'a//b', 'package.json', 'a b', '/'.join('d' * 9) + '/x', 'x/./y'):
            cases[f'unsafe {path}'] = (stream(manifest({path: b'x'}), b'x'), 'The update lists an unsafe path')
        for case, (data, problem) in cases.items():
            with self.subTest(case):
                self.assertEqual(self.stage(data), ['-1', problem])
                self.untouched()
        self.assertEqual(self.stage(stream(good, payload)[:-5], declared=len(stream(good, payload))), ['-4', 'The upload ended early'])
        self.untouched()
        self.assertEqual(self.stage(b'DISCUPD1', declared=update_file.PACKAGE_MAX * 2), ['-1', 'The update is too large'])
        (self.boot.parent/'refuse').touch()
        self.assertEqual(self.stage(stream(good, payload)), ['-1', 'The boot layer refused the update: the manifest does not fit'])
        self.untouched()
        (self.boot.parent/'refuse').unlink()
        # A keys file with a broken line trusts nothing.
        self.keys.write_text(ed25519.public_key(SECRET).hex() + '\nnot a key\n')
        self.assertEqual(self.stage(stream(good, payload)), ['-1', 'The update is not signed by a key this server trusts'])
        self.untouched()

    def test_a_request_for_boot_is_written_whole(self):
        request = self.root/'request'
        subprocess.run([*TOOL, 'request', str(request), 'activate'], check=True, timeout=30)
        self.assertEqual(json.loads(request.read_text()), {'action': 'activate'})
        self.assertFalse(Path(str(request) + '.tmp').exists())


class UpdateToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_ed25519_follows_rfc_8032(self):
        for secret, public, message, signature in VECTORS:
            secret, public, message, signature = map(bytes.fromhex, (secret, public, message, signature))
            self.assertEqual(ed25519.public_key(secret), public)
            self.assertEqual(ed25519.sign(secret, message), signature)
            self.assertTrue(ed25519.verify(public, message, signature))
            self.assertFalse(ed25519.verify(public, message + b'x', signature))

    def test_keygen_pack_and_inspect(self):
        key = self.root/'keys/update.key'
        public = update_file.keygen(key)
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        self.assertEqual(public, ed25519.public_key(update_file.read_key(key)).hex())
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            update_file.keygen(key)
        package = self.root/'package'
        for path, data in GOOD.items():
            (package/path).parent.mkdir(parents=True, exist_ok=True)
            (package/path).write_bytes(data)
        (package/'package.json').write_bytes(manifest(GOOD))
        result = update_file.pack(package, key, self.root/'out.update')
        self.assertEqual((result['name'], result['version'], result['files'], result['key']), ('disc-server', '2', 2, public))
        data = (self.root/'out.update').read_bytes()
        self.assertEqual(data, stream(manifest(GOOD), b''.join(GOOD.values()), update_file.read_key(key)))
        keys = self.root/'update-keys'; keys.write_text(public + '\n')
        self.assertEqual(update_file.inspect(self.root/'out.update', keys)['signedBy'], public)
        keys.write_text(ed25519.public_key(OTHER).hex() + '\n')
        self.assertIsNone(update_file.inspect(self.root/'out.update', keys)['signedBy'])
        (package/'bin/run').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'does not match package.json'):
            update_file.pack(package, key, self.root/'again.update')


if __name__ == '__main__':
    unittest.main()
