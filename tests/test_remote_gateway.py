import json
import unittest
from unittest.mock import patch
from day8_gateway import config, desktop, linux_tun
from test_vault_config import fixture


def metadata(identifier='a' * 64, **values):
    return dict(id=identifier, platform='linux', running=True, service='awpipeline-etl-dev-proxy',
                entrypoint=['python3', 'gateway'], command=['run-container-tun'], workdir='/app', **values)


class RemoteGatewayTests(unittest.TestCase):
    def test_updates_named_single_container_using_inspected_identity(self):
        row = metadata()
        with patch.object(desktop, 'docker', side_effect=[json.dumps(row), json.dumps({'ok': True, 'node_update_version': 2}), 'Applied and verified.']) as docker:
            desktop.send_packet('fd62', 'awpipeline-etl-dev-proxy', 'encrypted-packet')
        self.assertEqual(docker.call_args.args, ('fd62', 'exec', '-i', row['id'], 'python3', 'gateway', 'accept-node'))
        self.assertEqual(docker.call_args.kwargs['packet'], 'encrypted-packet')

    def test_discovery_includes_single_and_legacy_gateways_only(self):
        single = metadata()
        legacy = {**metadata('b' * 64), 'service': 'day8-gateway', 'command': ['run', '--auto-unlock']}
        other = {**metadata('c' * 64), 'entrypoint': ['other']}
        rows = '\n'.join(json.dumps(x) for x in (single, legacy, other))
        listing = f"single {single['id']}\nlegacy {legacy['id']}\nother {other['id']}"
        with patch.object(desktop, 'docker', side_effect=[listing, rows]):
            result = desktop.main(['targets', 'fd62'])
        self.assertEqual(result['targets'], ['legacy', 'single'])
        self.assertEqual(result['identities'], {'single': single['id'], 'legacy': legacy['id']})

    def test_old_tun_runtime_is_rejected_before_sending_a_node(self):
        with patch.object(desktop, 'docker', side_effect=[json.dumps(metadata()), json.dumps({'ok': True})]) as docker:
            with self.assertRaises(ValueError):
                desktop.send_packet('fd62', 'awpipeline-etl-dev-proxy', 'encrypted-packet')
        self.assertEqual(docker.call_count, 2)

    def test_legacy_non_tun_service_remains_updateable(self):
        row = {**metadata(), 'service': 'day8-gateway', 'command': ['run', '--auto-unlock']}
        with patch.object(desktop, 'docker', side_effect=[json.dumps(row), 'Applied and verified.']) as docker:
            desktop.send_packet('fd62', 'legacy', 'encrypted-packet')
        self.assertEqual(docker.call_count, 2)

    def test_container_tun_accepts_supported_custom_port_arguments(self):
        row = {**metadata(), 'command': ['run-container-tun', '--port', '7891']}
        self.assertTrue(desktop.managed_container(row))

    def test_stopped_or_replaced_containers_are_rejected_before_exec(self):
        for row in ({**metadata(), 'running': False}, metadata('b' * 64)):
            with patch.object(desktop, 'docker', return_value=json.dumps(row)) as docker:
                with self.assertRaises(ValueError):
                    desktop.send_packet('fd62', 'a' * 64, 'encrypted-packet')
                self.assertEqual(docker.call_count, 1)

    def test_tun_update_stages_both_bootstrap_nodes_then_removes_previous_node(self):
        base = fixture()
        cfg = config.native(base, tun=True)
        linux_tun.configure(cfg)
        node = {**base['day8'], 'server': '192.0.2.30', 'port': 8443}
        updated, staged = config.node_update(cfg, base, node)
        previous = config.bootstrap(base)
        replacement = config.bootstrap({**base, 'day8': node})
        self.assertIn(previous, cfg['rules'])
        self.assertNotIn(replacement, cfg['rules'])
        self.assertIn(previous, staged['rules'])
        self.assertIn(replacement, staged['rules'])
        self.assertNotIn(previous, updated['rules'])
        self.assertIn(replacement, updated['rules'])
        self.assertIn('DOMAIN-SUFFIX,cn,DIRECT', updated['rules'])
        self.assertIn('RULE-SET,cn-networks,DIRECT', updated['rules'])
        self.assertEqual(cfg['rule-providers'], updated['rule-providers'])
        self.assertEqual(cfg['proxies'], updated['proxies'])
        self.assertEqual(updated['rules'][-2:], ['MATCH,Day8-native', 'MATCH,REJECT'])

    def test_non_tun_and_unchanged_bootstrap_do_not_reload_core(self):
        base = fixture()
        for cfg, node in ((config.native(base), {**base['day8'], 'port': 8443}),
                          (config.native(base, tun=True), base['day8'])):
            updated, staged = config.node_update(cfg, base, node)
            self.assertIs(updated, cfg)
            self.assertIs(staged, cfg)
