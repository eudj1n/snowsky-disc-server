"""Version promotion and selection without firmware, Docker or external repos."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import firmware_profile as profiles
import emulator


class FirmwareProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = profiles.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.write(self.base)
        (self.directory/'active-version').write_text(self.base['version'])
        self.env = patch.dict('os.environ', {'FW_VERSION':''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, profile):
        (self.directory/f'v{profile["version"]}.json').write_text(json.dumps(profile))

    def future(self):
        p = copy.deepcopy(self.base)
        p.update(version='9.99', main_os_version=999, rootfs_sha256='a'*64,
                 rootfs_chunks=91, rootfs_size=91234567)
        self.write(p)
        return p

    def test_default_environment_and_explicit_selection(self):
        future = self.future()
        self.assertEqual(profiles.load_profile(directory=self.directory), self.base)
        with patch.dict('os.environ', {'FW_VERSION':future['version']}):
            self.assertEqual(profiles.load_profile(directory=self.directory), future)
            self.assertEqual(profiles.load_profile(self.base['version'], self.directory), self.base)

    def test_promote_new_version_without_script_edits(self):
        future = self.future()
        (self.directory/'active-version').write_text(future['version'])
        selected = profiles.load_profile(directory=self.directory)
        self.assertEqual(selected['main_os_version'], 999)
        self.assertEqual(profiles.artifact_names(selected)[0], 'disc-web-v999-review-only.bin')

    def test_unknown_malformed_and_inventory_only_versions_refused(self):
        (self.directory/'inventory').mkdir()
        (self.directory/'inventory/v8.88.json').write_text('{}')
        for version in ('../2.57', '8.88', '2.5', 'v2.57'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                profiles.load_profile(version, self.directory)

    def test_missing_identity_fingerprint_and_invalid_scenarios_refused(self):
        for key, value in [('rootfs_sha256','bad'), ('rootfs_chunks', True),
                           ('main_os_version', 1), ('stock_files', {'../escape':'a'*64}),
                           ('acceptance',['unimplemented']), ('writer','../writer')]:
            with self.subTest(key=key):
                changed = {**self.base, key:value}
                self.write(changed)
                with self.assertRaises(ValueError):
                    profiles.load_profile(self.base['version'], self.directory)

    def test_recorded_stack_ignores_new_default_and_refuses_changed_profile(self):
        state = {'firmwareVersion':self.base['version'], 'firmwareProfileSha256':profiles.fingerprint(self.base)}
        future = self.future()
        (self.directory/'active-version').write_text(future['version'])
        self.assertEqual(profiles.state_profile(state, self.directory), self.base)
        changed = {**self.base, 'rootfs_sha256':'b'*64}
        self.write(changed)
        with self.assertRaises(ValueError):profiles.state_profile(state, self.directory)
        with self.assertRaises(ValueError):profiles.state_profile({}, self.directory)

    def test_unreviewed_scenario_not_inherited(self):
        future = self.future()
        future['acceptance'] = ['smoke']
        profiles.require_scenario(future, 'smoke')
        with self.assertRaises(ValueError):profiles.require_scenario(future, 'idle')

    def test_the_guest_is_the_boot_layers_wrapper_with_this_repositorys_record(self):
        with tempfile.TemporaryDirectory() as temp:
            boot = Path(temp)
            (boot/'scripts').mkdir()
            (boot/'scripts/guest.py').write_text('')
            with patch.object(emulator, 'BOOT', boot), patch.object(emulator.subprocess, 'run') as run:
                emulator.guest('power', 'on', '--hold', 'play')
        args = run.call_args.args[0]
        self.assertEqual(args[1:], [str(boot/'scripts/guest.py'), '--state', str(emulator.STATE), 'power', 'on', '--hold', 'play'])

    def test_up_pins_this_repositorys_profile_in_the_record(self):
        # The guest's own record names the firmware; the checks also pin this repository's profile.
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)/'guest.json'

            def guest(*args, capture=False):
                if args[0] == 'up':
                    self.assertIn('--publish', args)
                    self.assertIn(f'platform={emulator.ROOT}', args)
                    state.write_text(json.dumps({'id': 'test', 'firmwareVersion': self.base['version']}))

            argv = ['emulator.py', 'up', '--reference', '/r', '--image', '/i.bin', '--ota', '/o']
            with patch.object(emulator, 'STATE', state), patch.object(emulator, 'guest', guest), \
                 patch.object(emulator, 'install'), patch.object(sys, 'argv', argv):
                emulator.main()
            record = json.loads(state.read_text())
        self.assertEqual(record['firmwareProfileSha256'], profiles.fingerprint(profiles.load_profile(self.base['version'])))
        self.assertEqual(profiles.state_profile(record), profiles.load_profile(self.base['version']))

    def test_guest_selection_does_not_shadow_reference_imports(self):
        spec = importlib.util.spec_from_file_location('selected_test', ROOT/'tests/integration/selected_firmware.py')
        selected = importlib.util.module_from_spec(spec)
        before = list(sys.path)
        with patch.dict('os.environ', {'CI_DISPOSABLE':'1', 'FW_VERSION':self.base['version']}):
            spec.loader.exec_module(selected)
        self.assertEqual(sys.path, before)
        self.assertEqual(selected.MAIN_OS, self.base['main_os_version'])

    def test_usb_profile_is_explicit_firmware_pinned_and_bounded(self):
        usb=profiles.load_usb_profile(self.base)
        directory=self.directory/'usb';directory.mkdir()
        target=directory/f'v{self.base["version"]}.json'
        for key,value in [('rootfs_sha256','a'*64),('session_seconds',901),
                          ('sd_mount','/tmp/sd;exec'),('udc','../../device')]:
            with self.subTest(key=key):
                target.write_text(json.dumps({**usb,key:value}))
                with self.assertRaises(ValueError):profiles.load_usb_profile(self.base,self.directory)
        target.unlink()
        with self.assertRaises(FileNotFoundError):profiles.load_usb_profile(self.base,self.directory)
        self.assertIn('usb-engineering',profiles.artifact_names(self.base,'usb-engineering')[0])
        self.assertNotIn('usb-engineering',profiles.artifact_names(self.base)[0])
        self.assertEqual(profiles.artifact_names(self.base,'product')[0], profiles.artifact_names(self.base)[0].replace('-review-only', '-product-review-only'))
        with self.assertRaises(ValueError): profiles.artifact_names(self.base, 'debug')

    def test_boot_report_policy_is_bounded_and_after_usb_deadline(self):
        usb=profiles.load_usb_profile(self.base)
        directory=self.directory/'usb';directory.mkdir()
        target=directory/f'v{self.base["version"]}.json'
        for key,value in [('delay_seconds',30),('delay_seconds',True),
                          ('wait_seconds',181),('wait_seconds',45),
                          ('max_bytes',16385),('max_bytes',False)]:
            with self.subTest(key=key,value=value):
                policy={**usb['boot_report'],key:value}
                target.write_text(json.dumps({**usb,'boot_report':policy}))
                with self.assertRaises(ValueError):profiles.load_usb_profile(self.base,self.directory)



if __name__ == '__main__':
    unittest.main()
