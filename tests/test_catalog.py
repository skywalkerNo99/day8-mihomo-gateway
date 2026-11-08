from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from day8_gateway import catalog, vault
from test_vault_config import fixture


class CatalogTests(unittest.TestCase):
    def test_current_route_comes_from_runtime_identity(self):
        base = fixture()
        a = dict(base['day8'], max_padding=1024)
        without_default = dict(a)
        del without_default['max_padding']
        b = dict(a, server_password='other')
        entries = [{'id': 'routeA', 'name': 'First', 'node': a, 'maintenance': False},
                   {'id': 'routeB', 'name': 'Next', 'node': b, 'maintenance': True}]
        key = bytes(32)
        view = catalog.summary(entries, key, catalog.token(key, without_default))
        self.assertEqual(view['activeName'], 'First')
        self.assertFalse(view['routes'][1]['available'])
        self.assertNotIn('node', view['routes'][0])
        self.assertIsNone(catalog.summary(entries, key, 'different')['activeName'])

    def test_import_keeps_last_good_catalog_on_invalid_input(self):
        import json
        base = fixture()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            profile, source, output = root/'base', root/'input', root/catalog.FILENAME
            vault.save(profile, base, 'test password')
            _, key = vault.unlock_with_password(vault.read_envelope(profile), 'test password')
            entry = {'id': 1, 'symbol': 'ROUTE_A', 'name': 'First', 'protocol': 'muon',
                     'encryption_method': 'aes-128-gcm', 'host': '192.0.2.1', 'port': 4321,
                     'encryption_password': 'sample server password'}
            doc = {'servers': [entry], 'credentials': {'password': 'sample user password'}}
            source.write_text(json.dumps(doc))
            self.assertEqual(catalog.save_export(source, profile, output, key, base), 1)
            before = output.read_bytes()
            self.assertNotIn(b'sample', before)
            self.assertEqual(catalog.read(root, key)[0]['node']['port'], 4321)
            doc['servers'].append(entry)
            source.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                catalog.save_export(source, profile, output, key, base)
            self.assertEqual(before, output.read_bytes())
