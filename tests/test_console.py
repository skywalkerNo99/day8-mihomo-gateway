from datetime import datetime, timedelta
import os
import time
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

    def test_login_service_accepts_no_legacy_clash_arguments(self):
        import io
        from contextlib import redirect_stderr
        with (patch('sys.argv', ['service', '--root', '/synthetic', '--profile', '/synthetic/clash.yaml']),
              patch.object(macos_service.macos_keychain, 'unlock') as unlock,
              redirect_stderr(io.StringIO()), self.assertRaises(SystemExit)):
            macos_service.main()
        unlock.assert_not_called()

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

    def test_connection_stdout_has_timestamp_timezone_and_redaction(self):
        line = '[TCP] local:12 --> example.com:443 match Match using Day8-native private'
        record = monitor.timestamped_connection_line(line, ['192.0.2.1'])
        stamp, message = record.split(' ', 1)
        self.assertIsNotNone(datetime.fromisoformat(stamp).utcoffset())
        self.assertEqual(message, '[TCP] example.com:443 via Day8-native')
        self.assertNotIn('private', record)
        hidden = '[TCP] local:12 --> 192.0.2.1:443 match Match using DIRECT'
        self.assertIsNone(monitor.timestamped_connection_line(hidden, ['192.0.2.1']))
        self.assertIsNone(monitor.timestamped_connection_line('parse error password=secret', []))

    def test_connection_timestamps_use_utc8_in_a_utc_container(self):
        try:
            with patch.dict(os.environ, {'TZ': 'UTC'}):
                time.tzset()
                record = monitor.timestamped_connection_line(
                    '[TCP] local:12 --> example.com:443 match Match using Day8-native', [])
        finally:
            time.tzset()
        stamp = record.split(' ', 1)[0]
        self.assertEqual(datetime.fromisoformat(stamp).utcoffset(), timedelta(hours=8))

    def test_remote_update_requires_managed_linux_container(self):
        with patch.object(desktop, 'docker', return_value='abc something-else linux') as docker:
            with self.assertRaises(ValueError):
                desktop.send_packet('default', 'other', 'test')
            self.assertEqual(docker.call_count, 1)
