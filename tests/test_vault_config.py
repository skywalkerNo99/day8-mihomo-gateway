import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from day8_gateway import config, vault


def fixture():
    return {'schema': 1, 'day8': {'server': '192.0.2.10', 'port': 443, 'cipher': 'aes-128-gcm',
            'server_password': 'test-server-key', 'user_password': 'test-user-key', 'max_padding': 32},
            'residual': {'server': '198.51.100.20', 'port': 1080, 'username': 'test-user', 'password': 'test-pass'},
            'expected_exit_ip': '198.51.100.20'}


class VaultTests(unittest.TestCase):
    def test_encryption_randomized_and_roundtrips(self):
        a = vault.encrypt(fixture(), 'synthetic-password')
        b = vault.encrypt(fixture(), 'synthetic-password')
        self.assertNotEqual(a, b)
        self.assertEqual(vault.decrypt(a, 'synthetic-password'), fixture())
        self.assertNotIn('192.0.2.10', json.dumps(a))
        self.assertNotIn('test-server-key', json.dumps(a))

    def test_wrong_password_and_modified_ciphertext_rejected(self):
        envelope = vault.encrypt(fixture(), 'synthetic-password')
        with self.assertRaises(vault.VaultError):
            vault.decrypt(envelope, 'wrong-password')
        raw = bytearray(vault.decode(envelope['ciphertext']))
        raw[-1] ^= 1
        envelope['ciphertext'] = vault.b64(raw)
        with self.assertRaises(vault.VaultError):
            vault.decrypt(envelope, 'synthetic-password')

    def test_modified_header_and_unbounded_kdf_rejected(self):
        envelope = vault.encrypt(fixture(), 'synthetic-password')
        envelope['nonce'] = vault.b64(b'x' * 12)
        with self.assertRaises(vault.VaultError):
            vault.decrypt(envelope, 'synthetic-password')
        envelope['kdf']['memory_kib'] = 2 ** 40
        with self.assertRaises(vault.VaultError):
            vault.decrypt(envelope, 'synthetic-password')

    def test_file_private_and_only_ciphertext_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.enc.json'
            vault.save(path, fixture(), 'synthetic-password')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(vault.load(path, 'synthetic-password'), fixture())
            self.assertEqual(list(Path(tmp).iterdir()), [path])


class RoutingTests(unittest.TestCase):
    def test_native_chain_is_fixed_and_udp_fails_closed(self):
        cfg = config.native(fixture())
        self.assertFalse(cfg['tun']['enable'])
        self.assertEqual(cfg['rules'][-2:], ['MATCH,Day8-native', 'MATCH,REJECT'])
        self.assertLess(cfg['rules'].index('NETWORK,UDP,REJECT'), cfg['rules'].index('MATCH,Day8-native'))
        self.assertEqual(cfg['proxies'][1]['dialer-proxy'], 'Day8-native')
        self.assertFalse(any(p['udp'] for p in cfg['proxies']))
        self.assertEqual(cfg['proxies'][0]['server'], '127.0.0.1')
        self.assertNotEqual(cfg['secret'], config.native(fixture())['secret'])

    def test_residential_routes_reject_failure_and_use_day8_dns(self):
        cfg = config.native(fixture())
        for name in cfg['rule-providers']:
            suffix = ',no-resolve' if name == 'apple-ips' else ''
            index = cfg['rules'].index(f'RULE-SET,{name},Residual-egress{suffix}')
            self.assertEqual(cfg['rules'][index+1], f'RULE-SET,{name},REJECT{suffix}')
        self.assertEqual(cfg['dns']['nameserver'], ['https://1.1.1.1/dns-query#Day8-native'])

    def test_tun_blocks_residential_udp_before_lan_direct(self):
        rules = config.native(fixture(), tun=True)['rules']
        guard = rules.index('AND,((NETWORK,UDP),(RULE-SET,residential-domains)),REJECT')
        self.assertTrue(all(guard < index for index, rule in enumerate(rules) if ',DIRECT' in rule))

    def test_legacy_macos_config_and_strict_mode_are_not_exposed(self):
        self.assertFalse(hasattr(config, 'macos'))
        with self.assertRaises(TypeError):
            config.native(fixture(), strict=True)


if __name__ == '__main__':
    unittest.main()
