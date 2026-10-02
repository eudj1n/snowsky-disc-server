"""The root OpenAPI document stays consistent with the served routes and the catalog schema."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts import command_catalog

LIVE = ['/api/health', '/api/catalog', '/api/catalog/stream', '/api/websocket', '/', '/{path}', '/apps/{app}/{path}', '/api/apps', '/api/contract/{name}',
        '/api/media/{kind}/{path}', '/api/media/current-lyrics', '/api/device', '/api/history', '/api/favorites/{songId}',
        '/api/store', '/api/store/{collection}/{operation}', '/api/trash', '/api/trash/{id}', '/api/trash/{id}/restore', '/api/about',
        '/api/card/leftovers', '/api/card/leftovers/trash', '/api/lists', '/api/lists/{scope}', '/api/lists/{scope}/{name}',
        '/api/card/folder/{folder}', '/api/card/tree/{folder}', '/api/apps/default', '/api/apps/{app}']
PLANNED = ['/api/data/{query}', '/api/stock/{route}']


class OpenApiTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT/'openapi.yaml').read_text()

    def has(self, pattern, what):
        self.assertIsNotNone(re.search(pattern, self.text, re.M), what)

    def test_document_declares_every_live_and_planned_route(self):
        self.assertTrue(self.text.startswith('openapi: 3.1.0'))
        for path in LIVE + PLANNED:
            self.has(rf'^  {re.escape(path)}:$', path)
        self.has(r'^  /api/stock/\{route\}:\n    x-stage: B$', 'planned routes are marked')

    def test_catalog_record_fields_match_the_schema(self):
        catalog = command_catalog.load_catalog()
        record = next(iter(catalog['records'].values()))
        route = catalog['http'][0]
        for name in record:
            self.has(rf'^        {name}: \{{', f'RecordCommand.{name}')
        for name in route:
            self.has(rf'^        {name}: \{{', f'HttpCommand.{name}')
        for code in ('1002', '1007', '1008', '1009', '1011'):
            self.assertIn(f"'{code}':", self.text)

    def test_parses_when_a_yaml_library_is_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML is not installed here; the document was validated with another parser')
        document = yaml.safe_load(self.text)
        self.assertEqual(sorted(document['paths']), sorted(LIVE + PLANNED))
        self.assertEqual(document['info']['version'], '1.0.0')


if __name__ == '__main__':
    unittest.main()
