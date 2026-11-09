import unittest
from unittest.mock import patch, Mock
from day8_gateway import cli, config, linux_tun
from test_vault_config import fixture


class LinuxContainerTunTests(unittest.TestCase):
    def test_container_entrypoint_unlocks_and_starts_the_complete_tun_stack(self):
        with patch.object(cli.sys, 'argv', ['gateway', 'run-container-tun']), \
             patch.object(cli.os, 'geteuid', return_value=0), \
             patch.object(cli.Path, 'is_file', return_value=True), \
             patch.object(cli.container_key, 'unlock', return_value=fixture()), \
             patch.object(cli.container_key, 'read_key', return_value=bytes(range(32))), \
             patch.object(cli.node_updates, 'load_saved'), \
             patch.object(linux_tun, 'prepare') as prepare, \
             patch.object(cli.runtime, 'start') as start:
            self.assertEqual(cli.main(), 0)
        prepare.assert_called_once()
        payload, cfg, args, detached = start.call_args.args
        self.assertFalse(detached)
        self.assertEqual(cfg['mixed-port'], 7890)
        self.assertTrue(cfg['tun']['enable'])
        self.assertEqual(cfg['dns']['listen'], '127.0.0.1:1053')
        self.assertTrue(args.auto_unlock)
        self.assertTrue(args.connection_logs)

    def test_shared_namespace_has_dns_listener_and_fixed_two_hop_policy(self):
        cfg = config.native(fixture(), port=7890, tun=True)
        linux_tun.configure(cfg)
        self.assertEqual(cfg['dns']['listen'], '127.0.0.1:1053')
        self.assertEqual(cfg['tun']['device'], 'day8tun0')
        self.assertEqual(cfg['rules'][-2:], ['MATCH,Day8-native', 'MATCH,REJECT'])
        self.assertEqual(cfg['proxies'][1]['dialer-proxy'], 'Day8-native')
        self.assertIn('NETWORK,UDP,REJECT', cfg['rules'])
        self.assertIn(config.bootstrap(fixture()), cfg['rules'])
        self.assertTrue(cfg['rule-providers']['cn-networks']['payload'])
        domain = cfg['rules'].index('DOMAIN-SUFFIX,cn,DIRECT')
        subnet = cfg['rules'].index('RULE-SET,cn-networks,DIRECT')
        self.assertLess(cfg['rules'].index('NETWORK,UDP,REJECT'), domain)
        for name in ('residential-domains', 'apple-ips'):
            suffix = ',no-resolve' if name == 'apple-ips' else ''
            self.assertLess(cfg['rules'].index(f'RULE-SET,{name},REJECT{suffix}'), domain)
        self.assertLess(domain, subnet)
        self.assertLess(subnet, len(cfg['rules']) - 2)

    def test_kernel_guard_rejects_public_udp_without_flushing_other_rules(self):
        with patch.object(linux_tun.sys, 'platform', 'linux'), \
             patch.object(linux_tun.os, 'geteuid', return_value=0), \
             patch.object(linux_tun.Path, 'is_file', return_value=True), \
             patch.object(linux_tun.Path, 'is_char_device', return_value=True), \
             patch.object(linux_tun.Path, 'read_text', return_value='1\n'), \
             patch.object(linux_tun.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            linux_tun.prepare()
        restore = next(call for call in run.call_args_list if call.args[0][0] == 'iptables-restore')
        self.assertIn('--noflush', restore.args[0])
        self.assertIn('-A DAY8_UDP -j REJECT --reject-with icmp-port-unreachable', restore.kwargs['input'])
        self.assertNotIn('-F OUTPUT', restore.kwargs['input'])
        calls = [c.args[0] for c in run.call_args_list]
        self.assertTrue(any('REDIRECT' in c and '1053' in c and 'tcp' in c for c in calls))
        self.assertTrue(any('REDIRECT' in c and '1053' in c and 'udp' in c for c in calls))

    def test_container_mode_refuses_other_platforms_without_changing_network(self):
        with patch.object(linux_tun.sys, 'platform', 'darwin'), patch.object(linux_tun.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                linux_tun.prepare()
        run.assert_not_called()
