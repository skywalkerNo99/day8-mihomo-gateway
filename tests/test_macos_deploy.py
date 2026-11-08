from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from day8_gateway import cli
from test_vault_config import fixture


class MacDeployTests(unittest.TestCase):
    def deploy(self, trace_ok):
        with TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'profile.yaml'
            profile.write_text('rules: ["MATCH,DIRECT"]\n')
            core = Path(tmp) / 'core.yaml'
            core.write_text('external-controller: 127.0.0.1:9090\n')
            live = {'tun': {'enable': True}, 'mode': 'rule', 'mixed-port': 12345}
            argv = ['gateway', 'macos', 'deploy', '--profile', str(profile), '--core-config', str(core),
                    '--block-public-udp', '--apply']
            with (patch('sys.argv', argv), patch.object(cli, 'unlock', return_value=fixture()),
                  patch.object(cli.controller, 'request', return_value=live) as api,
                  patch.object(cli.controller, 'apply') as apply,
                  patch.object(cli.runtime, 'start'),
                  patch.object(cli.runtime, 'control', return_value={'ok': trace_ok}) as control,
                  redirect_stdout(io.StringIO())):
                if trace_ok:
                    self.assertEqual(cli.main(), 0)
                    self.assertEqual(control.call_count, 1)
                else:
                    with self.assertRaises(cli.UserError):
                        cli.main()
                    self.assertEqual(api.call_args.args[2:], ('/configs?force=true', {'path': str(profile.resolve())}, 'PUT'))
                    self.assertEqual(control.call_args.args[1], 'stop')
                cfg = apply.call_args.args[2]
                self.assertTrue(cfg['tun']['enable'])
                self.assertEqual(cfg['rules'][-2], 'MATCH,Day8-native')
                self.assertIn('NETWORK,UDP,REJECT', cfg['rules'])
                self.assertEqual(cfg['rules'][0], 'AND,((NETWORK,UDP),(RULE-SET,residential-domains)),REJECT')
                self.assertEqual(control.call_args_list[0].kwargs['proxy'], 'http://127.0.0.1:12345')

    def test_verified_split(self):
        self.deploy(True)

    def test_trace_mismatch_restores_profile(self):
        self.deploy(False)
