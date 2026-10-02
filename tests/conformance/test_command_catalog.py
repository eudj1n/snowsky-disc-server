"""Reviewed command catalog invariants; synthetic, no firmware or device."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import command_catalog as cc, firmware_profile

# Everything the reference disc_web music player does through Controller/Library.
DISC_WEB_CAPABILITIES = {
    'identity_handshake', 'settings', 'now_playing', 'play_mode', 'queue_page', 'tracks_page', 'artists_page',
    'albums_page', 'genres_page', 'album_tracks_page', 'artist_tracks_page', 'playlist_tracks_page', 'folders_page',
    'control', 'favorite', 'seek', 'set_play_mode', 'set_volume', 'select_position', 'play_all', 'library_scan',
    'gain_read', 'balance_read', 'filter_read', 'dre_read', 'set_gain', 'set_balance', 'set_filter', 'set_dre',
    'peq_read', 'set_peq', 'eq_type_read', 'set_eq_type', 'eq_master_read', 'set_eq_master',
    'catalog_page', 'current_cover', 'upload_audio', 'create_folder', 'upload_progress', 'transfer_browse',
    'playlist_command', 'playlist_add_range', 'playlist_remove',
}


class CommandCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = cc.load_catalog()
        self.profile = firmware_profile.load_profile()

    def names(self):
        return {e['name'] for e in self.catalog['records'].values()} | {r['name'] for r in self.catalog['http']}

    def test_disc_web_capabilities_are_all_described(self):
        self.assertEqual(DISC_WEB_CAPABILITIES - self.names(), set())

    def test_installed_read_only_surface_matches_service_contract(self):
        r = self.catalog['records']
        self.assertEqual((r['0599']['reply'], r['0501']['reply'], r['0202']['reply'], r['0105']['reply']),
                         ('a599', 'a501', 'a202', 'a102'))
        self.assertTrue(r['0202']['silent_ok'])
        self.assertTrue(all(r[t]['kind'] == 'read' for t in ('0599', '0501', '0202', '0105')))

    def test_record_admission_follows_payload_patterns_and_denylist(self):
        c = self.catalog
        self.assertEqual(cc.admits_record(c, '0201', '0001')['name'], 'control')
        self.assertIsNone(cc.admits_record(c, '0201', '0003'))
        self.assertEqual(cc.admits_record(c, '0103', '00003A98')['name'], 'seek')
        self.assertIsNone(cc.admits_record(c, '0103', '3A98'))
        self.assertEqual(cc.admits_record(c, '0100', '00010003CI Album')['name'], 'select_position')
        self.assertEqual(cc.admits_record(c, '0101', '0005{"id":0}')['name'], 'play_all')
        self.assertEqual(cc.admits_record(c, '0599', '0000')['name'], 'identity_handshake')
        self.assertIsNone(cc.admits_record(c, '0599', '0001'))
        self.assertIsNone(cc.admits_record(c, '0621', '0000'))
        self.assertIsNone(cc.admits_record(c, '0800', ''))
        self.assertIsNone(cc.admits_record(c, '0678', 'x' * 2001))
        self.assertEqual(cc.admits_record(c, '0622', '0000')['class'], 'scan')

    def test_http_admission_requires_headers_and_denials_win(self):
        c = self.catalog
        ok = cc.admits_route(c, 'GET', '/song_category_tree/', {'type': 'all/song', 'start-pos': '0', 'num-max': '20'})
        self.assertEqual(ok['name'], 'catalog_page')
        self.assertIsNone(cc.admits_route(c, 'GET', '/song_category_tree/', {'type': 'all/song', 'start-pos': '0'}))
        self.assertIsNone(cc.admits_route(c, 'GET', '/song_category_tree/', {'type': 'shell', 'start-pos': '0', 'num-max': '20'}))
        self.assertEqual(cc.admits_route(c, 'POST', '/audio/tmp/sdcard/Album/Track.flac', {})['name'], 'upload_audio')
        self.assertIsNone(cc.admits_route(c, 'POST', '/audio/tmp/sdcard/../etc/passwd', {}))
        self.assertIsNone(cc.admits_route(c, 'DELETE', '/file/tmp/sdcard/Track.flac', {}))
        self.assertIsNone(cc.admits_route(c, 'DELETE', '/song_category_tree/', {'type': 'custom/song', 'src_list_id': '0', 'delete_source': '1'}))
        self.assertIsNone(cc.admits_route(c, 'DELETE', '/song_category_tree/', {'type': 'all/song', 'delete_source': '0'}))
        self.assertEqual(cc.admits_route(c, 'DELETE', '/song_category_tree/', {'type': 'custom', 'delete_source': '0'})['name'], 'playlist_remove')
        self.assertIsNone(cc.admits_route(c, 'POST', '/image/tmp/sdcard/a.png', {}))
        self.assertIsNone(cc.admits_route(c, 'GET', '/song_category_tree/?type=all', {'type': 'all/song', 'start-pos': '0', 'num-max': '20'}))

    def test_every_mutation_is_paced_and_every_read_has_a_reply(self):
        for tag, e in self.catalog['records'].items():
            if e['kind'] == 'mutation':
                self.assertGreater(e['pacing_ms'], 0, tag)
            else:
                self.assertIsNotNone(e['reply'], tag)
        for r in self.catalog['http']:
            self.assertEqual(r['kind'] == 'mutation', r['method'] != 'GET', r['name'])

    def test_published_commands_json_carries_provenance(self):
        data = json.loads(cc.commands_data(self.profile, self.catalog))
        self.assertEqual(data['profile_sha256'], firmware_profile.fingerprint(self.profile))
        self.assertEqual(data['catalog_sha256'], cc.fingerprint(self.catalog))
        self.assertEqual(len(data['records']), len(self.catalog['records']))
        with self.assertRaisesRegex(ValueError, 'disagree'):
            cc.commands_data(dict(self.profile, version='2.58'), self.catalog)

    def test_data_mutations_are_admitted_by_name_only(self):
        catalog = cc.load_catalog()
        self.assertEqual([entry['name'] for entry in catalog['data']], ['favorite_add'])
        for change in ({'name': 'drop_songs'}, {'pacing_ms': 0}, {'kind': 'read'}, {'sql': 'DELETE FROM SONG'}):
            with self.subTest(change=change):
                broken = copy.deepcopy(catalog)
                broken['data'][0].update(change)
                with self.assertRaises(ValueError):
                    cc.validate_data(broken['data'][0])

    def test_tampered_catalogs_are_rejected(self):
        def rejected(mutate, message):
            broken = copy.deepcopy(self.catalog); mutate(broken)
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / 'v2.57.json').write_text(json.dumps(broken))
                with self.assertRaisesRegex(ValueError, message):
                    cc.load_catalog(directory=Path(d))
        rejected(lambda c: c['records'].__setitem__('0621', dict(c['records']['0622'], name='reset')), 'never admitted')
        rejected(lambda c: c['denied']['records'].pop('0800'), 'must be listed')
        rejected(lambda c: c['records']['0201'].__setitem__('pacing_ms', 0), 'need pacing')
        rejected(lambda c: c['records']['0501'].__setitem__('reply', None), 'need an expected reply')
        rejected(lambda c: c['records'].__setitem__('a599', c['records']['0599']), 'starting with 0')
        rejected(lambda c: c['http'].append(dict(c['http'][0], name='catalog_query', path='/song_category_tree/\\?type=all')), 'query strings')
        rejected(lambda c: c['http'].append(dict(c['http'][5], name='file_delete', method='DELETE', path='/file/.*')), 'never be admitted')
        rejected(lambda c: c['records']['0599'].__setitem__('evidence', ''), 'evidence is required')
        rejected(lambda c: c['records']['0201'].__setitem__('payload', '(?:000)[0-2]'), 'POSIX ERE')
        rejected(lambda c: c['http'][0]['headers'].__setitem__('num-max', '\\d{1,3}'), 'POSIX ERE')
        rejected(lambda c: c.__setitem__('version', '2.58'), 'identity')
        # musl TRE compile cost: large counted bounds, nullable repeats and oversized expansions.
        rejected(lambda c: c['http'][0]['headers'].__setitem__('artist', '.{0,255}'), 'limited to')
        rejected(lambda c: c['http'][2].__setitem__('path', '/dir/tmp/sdcard/([^/]{1,255}/)*'), 'limited to')
        rejected(lambda c: c['records']['0201'].__setitem__('payload', '(0?)*'), 'empty string')
        rejected(lambda c: c['records']['0201'].__setitem__('payload', '((.{16}){16}){5}'), 'expands to')

    def test_chunked_bounds_match_exact_lengths(self):
        """The chunked forms accept exactly the lengths of the bounds they replace."""
        forms = {(0, 255): '(.{16}){0,15}.{0,15}', (1, 255): '.{1,15}(.{16}){0,15}|(.{16}){1,15}'}
        for (low, high), form in forms.items():
            compiled = cc.pattern(form, 'chunked form')
            for n in range(0, 400):
                self.assertEqual(bool(compiled.fullmatch('%' * n)), low <= n <= high, (form, n))
        payload = cc.pattern(self.catalog['records']['0412']['payload'], '0412')
        self.assertTrue(payload.fullmatch('0000' + 'Я' * 255))
        self.assertFalse(payload.fullmatch('0000' + 'Я' * 256))
        self.assertFalse(payload.fullmatch('0000'))

    def test_reviewed_patterns_are_cheap_for_musl(self):
        values = [e['payload'] for e in self.catalog['records'].values()]
        for route in self.catalog['http'] + self.catalog['denied']['http']:
            values.append(route['path']); values.extend(route.get('headers', {}).values())
        for value in values:
            self.assertLessEqual(cc.expanded_positions(cc.sre_parse.parse(value, cc.re.DOTALL), value), cc.MAX_EXPANDED)
            self.assertNotRegex(value, r'\{\d*,?(1[7-9]|[2-9]\d|\d{3,})\}')

    def test_long_path_components_are_left_to_the_filesystem(self):
        name = 'x' * 300
        self.assertIsNotNone(cc.admits_route(self.catalog, 'GET', f'/dir/tmp/sdcard/{name}/',
                                             {}))
        self.assertIsNone(cc.admits_route(self.catalog, 'GET', '/dir/tmp/sdcard//x/', {}))


if __name__ == '__main__':
    unittest.main()
