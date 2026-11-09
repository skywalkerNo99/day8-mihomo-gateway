import json
import plistlib
from pathlib import Path
import unittest
import asyncio
import tempfile
from types import SimpleNamespace

from unittest.mock import patch, AsyncMock, MagicMock

from day8_gateway import desktop, single_app, macos_service, runtime
from test_vault_config import fixture


class CaptureTests(unittest.TestCase):
    def test_desktop_always_verifies_system_tun_without_a_legacy_marker(self):
        with patch.object(Path, 'is_file', return_value=False), patch.object(desktop.runtime, 'control') as control:
            control.return_value = {'ok': True}
            self.assertEqual(desktop.main(['verify']), {'ok': True})
            control.assert_called_once_with(desktop.ROOT, 'verify-system-tun')

    def test_login_service_always_starts_complete_routing_core(self):
        args = SimpleNamespace(root=Path('/synthetic'), update_key=bytes(32), adapter_port=17899)
        probes = MagicMock()
        probes.run = AsyncMock()
        serve = AsyncMock()
        with (patch.object(Path, 'is_file', return_value=False),
              patch.object(macos_service.scheduled_probes, 'ProbeSchedule', return_value=probes),
              patch.object(macos_service.runtime, 'serve', serve)):
            asyncio.run(macos_service.supervise(fixture(), args))
        payload, cfg, actual_args = serve.call_args.args
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['listeners'][0]['name'], 'system-tun')
        self.assertIsInstance(actual_args.uplink, single_app.Uplink)
        self.assertEqual(actual_args.port, single_app.PORT)

    def test_exit_button_does_not_decode_connection_rows_as_catalog_routes(self):
        with patch.object(Path, 'is_file', return_value=True), patch.object(desktop.runtime, 'control') as control:
            control.return_value = {'ok': True, 'routes': [{'host': 'example.com', 'ok': True}]}
            self.assertEqual(desktop.main(['verify']), {'ok': True})
            control.assert_called_once_with(desktop.ROOT, 'verify-system-tun')

    def test_only_the_unprivileged_core_has_upstream_credentials(self):
        assets = Path(__file__).resolve().parents[1] / 'macos/tun'
        key = bytes(range(32))
        handoff = single_app.link_key(key)
        capture = json.loads((assets / 'capture.json.in').read_text().replace('__HANDOFF_KEY__', handoff))
        routing = single_app.routing(fixture(), key, 17899)
        gateway, = capture['proxies']
        listener, = routing['listeners']
        self.assertEqual((gateway['server'], gateway['port'], gateway['cipher'], gateway['password']),
                         (listener['listen'], listener['port'], listener['cipher'], listener['password']))
        self.assertFalse(capture.get('external-controller'))
        self.assertFalse(capture['allow-lan'])
        self.assertTrue(capture['profile']['store-fake-ip'])
        self.assertTrue(capture['tun']['auto-route'])
        self.assertFalse(routing['tun']['enable'])
        self.assertEqual(capture['rules'][0], 'NETWORK,UDP,REJECT')
        self.assertEqual(routing['rules'][0], 'NETWORK,UDP,REJECT')
        self.assertEqual(capture['rules'][1:], ['MATCH,Gateway', 'MATCH,REJECT'])
        self.assertEqual(routing['proxies'][1]['dialer-proxy'], 'Day8-native')
        self.assertEqual(routing['rules'][-2:], ['MATCH,Day8-native', 'MATCH,REJECT'])
        job = plistlib.loads((assets / 'io.day8gateway.tun.plist').read_bytes())
        self.assertTrue(job['KeepAlive'] and job['RunAtLoad'])
        self.assertEqual(job['ProgramArguments'], ['/Library/PrivilegedHelperTools/io.day8gateway.tun/run.sh'])
        device = capture['tun']['device']
        self.assertIn('block drop out quick on ! ' + device + ' proto udp', (assets / 'public-udp.pf').read_text())


class RuntimeModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_only_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            args = SimpleNamespace(root=Path(folder), adapter_port=0)
            with self.assertRaises(ValueError):
                await asyncio.wait_for(runtime.serve(fixture(), None, args), .1)
