import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from day8_gateway import desktop, lan_proxy, macos_service, single_app
from test_vault_config import fixture


class LanProxyTests(unittest.TestCase):
    def test_rejects_public_broad_and_invalid_networks(self):
        for value in ('0.0.0.0/0', '10.0.0.0/8', '192.0.2.0/24', '127.0.0.0/8',
                      '::1/128', '192.168.1.3/24', 'not-a-network', True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lan_proxy.network(value)

    def test_only_proxy_access_changes_and_existing_routing_stays_intact(self):
        cfg = single_app.routing(fixture(), bytes(32), 17899)
        previous = json.loads(json.dumps(cfg))
        cfg.update(lan_proxy.fields('192.168.20.0/24'))
        self.assertEqual(cfg['lan-allowed-ips'], ['127.0.0.0/8', '192.168.20.0/24'])
        self.assertTrue(cfg['allow-lan'])
        self.assertEqual(cfg['bind-address'], '0.0.0.0')
        for field in ('rules', 'proxies', 'dns', 'external-controller', 'listeners', 'tun'):
            self.assertEqual(cfg[field], previous[field])
        self.assertEqual(cfg['rules'][0], 'NETWORK,UDP,REJECT')
        cfg.update(lan_proxy.fields(None))
        self.assertFalse(cfg['allow-lan'])
        self.assertEqual(cfg['bind-address'], '127.0.0.1')

    def test_persistence_is_opt_in_private_and_removable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lan-proxy.json'
            self.assertIsNone(lan_proxy.load(path))
            lan_proxy.save(path, '192.168.20.0/24')
            self.assertEqual(lan_proxy.load(path), '192.168.20.0/24')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('{"network":"0.0.0.0/0"}')
            with self.assertRaises(ValueError):
                lan_proxy.load(path)
            lan_proxy.save(path, None)
            self.assertIsNone(lan_proxy.load(path))

    def test_service_restores_saved_access_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.local').mkdir()
            lan_proxy.save(root / '.local/lan-proxy.json', '192.168.20.0/24')
            args = SimpleNamespace(root=root, update_key=bytes(32), adapter_port=17899)
            probes = MagicMock()
            probes.run = AsyncMock()
            serve = AsyncMock()
            with (patch.object(macos_service.scheduled_probes, 'ProbeSchedule', return_value=probes),
                  patch.object(macos_service.runtime, 'serve', serve)):
                asyncio.run(macos_service.supervise(fixture(), args))
            cfg = serve.call_args.args[1]
            self.assertTrue(cfg['allow-lan'])
            self.assertEqual(cfg['lan-allowed-ips'], ['127.0.0.0/8', '192.168.20.0/24'])

    def test_desktop_only_sends_validated_access_settings(self):
        with patch.object(desktop.runtime, 'control', return_value={'ok': True}) as control:
            desktop.main(['enable-lan-proxy', '192.168.20.0/24'])
            control.assert_called_once_with(desktop.ROOT, 'configure-lan-proxy', network='192.168.20.0/24')
            control.reset_mock()
            with self.assertRaises(ValueError):
                desktop.main(['enable-lan-proxy', '0.0.0.0/0'])
            control.assert_not_called()
            desktop.main(['disable-lan-proxy'])
            control.assert_called_once_with(desktop.ROOT, 'configure-lan-proxy', network=None)
