"""The guest battery overlay reproduces the player's sysfs layout (no Docker)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'scripts/runtime'))
import firmware_profile as profiles
import battery_overlay

# What the external emulator's setup writes (no trailing newlines), plus the
# status its boot script rewrites.
EMULATOR = {'type': 'Battery', 'capacity': '100', 'status': 'Charging\n', 'health': 'Good',
            'present': '1', 'technology': 'Li-ion', 'voltage_now': '4200000', 'temp': '250',
            'online': '1'}


class BatteryOverlayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = profiles.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.battery = profiles.load_os_profile(self.base)['battery']
        self.directory = self.root/self.battery['path'].lstrip('/')
        self.directory.mkdir(parents=True)
        for name, value in EMULATOR.items():
            (self.directory/name).write_text(value)

    def read(self, name):
        return (self.directory/name).read_text()

    def test_guest_gets_the_player_layout_and_keeps_stock_inputs(self):
        names = battery_overlay.apply(self.root, self.battery)
        self.assertEqual(names, sorted(self.battery['attributes']))
        for name in ('status', 'online'):
            self.assertFalse((self.directory/name).exists())
        self.assertEqual(self.read('type'), 'Mains\n')
        self.assertEqual(self.read('current_now'), '0\n')
        self.assertEqual(self.read('cycle_count'), '0\n')
        # Stock reads capacity and temp; the emulator's values stay as they were.
        self.assertEqual(self.read('capacity'), '100')
        self.assertEqual(self.read('temp'), '250')

    def test_reapplying_after_a_boot_rewrote_status_is_idempotent(self):
        first = battery_overlay.apply(self.root, self.battery)
        (self.directory/'status').write_text('Discharging\n')
        self.assertEqual(battery_overlay.apply(self.root, self.battery), first)
        self.assertFalse((self.directory/'status').exists())

    def test_missing_directory_or_unknown_attribute_is_refused(self):
        with self.assertRaises(ValueError):
            battery_overlay.apply(self.root/'elsewhere', self.battery)
        extra = {**self.battery, 'attributes': self.battery['attributes']+['charge_full']}
        with self.assertRaises(ValueError):
            battery_overlay.apply(self.root, extra)

    def test_cli_requires_the_disposable_emulator(self):
        env = {k: v for k, v in os.environ.items() if k != 'CI_DISPOSABLE'}
        run = subprocess.run([sys.executable, str(ROOT/'scripts/runtime/battery_overlay.py'), '--root', str(self.root)],
                             env=env, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('Disposable emulator required', run.stderr)
        self.assertTrue((self.directory/'status').exists())

    def test_guest_service_applies_it_before_the_companion_starts(self):
        script = (ROOT/'scripts/guest-service.sh').read_text()
        overlay = script.index('battery_overlay.py --root "$ROOTFS" --version "$FW_VERSION"')
        self.assertLess(overlay, script.index('nohup'))


class OsProfileTests(unittest.TestCase):
    """The OS-level sources the player exposes (battery gauge, ALSA card), per firmware."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.base = profiles.load_profile((ROOT/'firmware/active-version').read_text().strip())
        self.original = profiles.load_os_profile(self.base)
        (self.directory/'os').mkdir()
        self.target = self.directory/'os'/f'v{self.base["version"]}.json'

    def load(self, profile):
        self.target.write_text(json.dumps(profile))
        return profiles.load_os_profile(self.base, self.directory)

    def test_player_battery_source_is_recorded(self):
        battery = self.original['battery']
        self.assertEqual(battery['path'], '/sys/class/power_supply/cw221X-bat')
        self.assertEqual(battery['type'], 'Mains')
        self.assertIn('cycle_count', battery['attributes'])
        self.assertEqual(sorted(battery['absent']), ['online', 'status'])
        self.assertEqual(self.original['asound'], {'card': '/proc/asound/card0'})
        self.assertEqual(profiles.os_service_args(self.original),
                         ['--battery-dir', '/sys/class/power_supply/cw221X-bat', '--asound-dir', '/proc/asound/card0',
                          '--player-process', 'mq_player', '--mdns-name', 'ingenic'])
        # The player's own mDNS name (combined-008): one DNS label, nothing else.
        for name in ('Ingenic', 'a.local', '-x', 'x-', '', 'a' * 64, None):
            with self.subTest(mdns=name):
                with self.assertRaises(ValueError):
                    self.load({**self.original, 'mdns': {'name': name}})
        for process in ('mq player', '../mq_player', '', 'a' * 16, None):
            with self.subTest(process=process):
                with self.assertRaises(ValueError):
                    self.load({**self.original, 'player': {'process': process}})
        self.assertEqual(self.load(self.original), self.original)
        for card in ('/proc/asound/../card0', '/proc/self/root', '/proc/asound/card0/pcm3p', None):
            with self.subTest(card=card):
                with self.assertRaises(ValueError):
                    self.load({**self.original, 'asound': {'card': card}})

    def test_mismatched_or_unsafe_profiles_are_refused(self):
        cases = [('rootfs_sha256', 'a'*64), ('path', '/sys/class/power_supply/../../etc'),
                 ('path', '/proc/self'), ('type', 'Mains; reboot'),
                 ('attributes', ['capacity', 'type', '../uevent']), ('attributes', ['temp']),
                 ('absent', ['status', 'capacity']), ('absent', 'status'), ('extra', 1)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                profile = copy.deepcopy(self.original)
                if key == 'rootfs_sha256':
                    profile[key] = value
                else:
                    profile['battery'][key] = value
                with self.assertRaises(ValueError):
                    self.load(profile)

    def test_missing_profile_is_not_inherited(self):
        with self.assertRaises(FileNotFoundError):
            profiles.load_os_profile(self.base, self.directory)


if __name__ == '__main__':
    unittest.main()
