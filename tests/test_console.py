from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidTag

from day8_gateway import desktop, macos_service, monitor, node_updates, official
from test_vault_config import fixture


class ConsoleTests(unittest.TestCase):
    def test_update_preserves_residential_and_binds_to_vault(self):
        base = fixture()
        key = bytes(range(32))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'base'
            profile.write_text('synthetic vault A')
            node = dict(base['day8'], port=5555)
            packet = node_updates.seal(profile, key, base, node)
            node_updates.save(root / '.local/node.enc', packet)
            node_updates.load_saved(root, profile, key, base)
            self.assertEqual(base['day8'], node)
            self.assertEqual(base['residual'], fixture()['residual'])
            self.assertEqual((root / '.local/node.enc').stat().st_mode & 0o777, 0o600)
            profile.write_text('synthetic vault B')
            with self.assertRaises(InvalidTag):
                node_updates.load_saved(root, profile, key, base)
            with self.assertRaises(ValueError):
                node_updates.seal(profile, key, base, {**node, 'residual': {}})

    def test_official_update_requires_unambiguous_outbound(self):
        node = fixture()['day8']
        outbound = {**node, 'label': 'first', 'protocol': 'muon'}
        self.assertEqual(official.selected({'outbounds': [outbound]}), node)
        self.assertIsNone(official.selected({'outbounds': [outbound, outbound]}))
        self.assertIsNone(official.selected({'router': {}}))

    def test_launch_profile_only_allows_reject(self):
        profile = macos_service.startup_config({'proxies':[fixture()['residual']], 'mixed-port': 7890}, 'http://127.0.0.1:9090', '')
        self.assertNotIn('proxies', profile)
        self.assertEqual(profile['proxy-groups'][0]['proxies'], ['REJECT'])
        self.assertEqual(profile['rules'], ['MATCH,' + macos_service.LOCK])
        self.assertTrue(profile['tun']['enable'])

    def test_probe_failures_independent(self):
        def reply(url, secret, path):
            if path.startswith('/proxies/Residual-egress/'):
                raise OSError('synthetic failure')
            return {} if path == '/connections' else {'delay': 123}
        with patch.object(monitor.controller, 'request', side_effect=reply):
            result = monitor.snapshot('http://127.0.0.1', '')
        self.assertTrue(result['nodes']['Day8-native']['online'])
        self.assertFalse(result['nodes']['Residual-egress']['online'])

    def test_connection_logs_only_return_sanitized_fields(self):
        line = '[TCP] local:12 --> 192.0.2.1:99 match Match using Day8-native private'
        self.assertEqual(monitor.connection_line(line, ['192.0.2.1']), '[TCP] <upstream> via Day8-native')
        self.assertIsNone(monitor.connection_line('parse error password=secret', []))

    def test_remote_update_requires_managed_linux_container(self):
        with patch.object(desktop, 'docker', return_value='abc something-else linux') as docker:
            with self.assertRaises(ValueError):
                desktop.send_packet('default', 'other', 'test')
            self.assertEqual(docker.call_count, 1)
