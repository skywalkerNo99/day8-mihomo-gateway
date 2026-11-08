from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from day8_gateway import config, domain_policy, monitor
from test_vault_config import fixture


class RulesTests(unittest.TestCase):
    def test_apply_persist_edit_delete_conflict(self):
        cfg = config.native(fixture())
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rules.enc'
            policy = domain_policy.Policy(cfg, path, bytes(32))
            def change(action, index, target='Residual-egress'):
                return policy.change({'revision': policy.snapshot()['revision'], 'action': action, 'index': index,
                                      'rule': {'kind': 'suffix', 'value': '*.Example.COM.', 'target': target}})
            rows = change('save', -1)
            rules = policy.rules(rows)
            self.assertEqual(rules[0], 'NETWORK,UDP,REJECT')
            self.assertEqual(rules[-6:-4], ['DOMAIN-SUFFIX,example.com,Residual-egress', 'DOMAIN-SUFFIX,example.com,REJECT'])
            self.assertIn('RULE-SET,residential-domains,Residual-egress', rules[:-4])
            policy.persist(rows)
            self.assertNotIn(b'example.com', path.read_bytes())
            self.assertEqual(domain_policy.Policy(cfg, path, bytes(32)).rows, rows)
            with self.assertRaises(ValueError):
                policy.change({'revision': 'stale'})
            with self.assertRaises(ValueError):
                change('save', -1)
            policy.persist(change('save', 0, 'REJECT'))
            self.assertEqual(policy.rows[0]['target'], 'REJECT')
            self.assertEqual(policy.rules(change('delete', 0)), policy.rules([]))

    def test_rule_order_selects_a_valid_default_probe(self):
        with TemporaryDirectory() as tmp:
            policy = domain_policy.Policy(config.native(fixture()), Path(tmp) / 'rules.enc', bytes(32))
            block = domain_policy.rule('keyword', 'example', 'REJECT')
            ordinary = domain_policy.rule('exact', 'example.com', 'Day8-native')
            policy.persist([block, ordinary])
            self.assertEqual(policy.ordinary_probe(), 'www.iana.org')
            rows = policy.change({'action': 'up', 'index': 1, 'revision': policy.snapshot()['revision']})
            policy.persist(rows)
            self.assertEqual(policy.ordinary_probe(), 'example.com')

    def test_direct_is_scoped_and_stays_after_protections(self):
        cfg = config.native(fixture())
        with TemporaryDirectory() as tmp:
            policy = domain_policy.Policy(cfg, Path(tmp) / 'rules.enc', bytes(32))
            rows = [domain_policy.rule('suffix', 'example.com', 'DIRECT')]
            for interface in ('en0', 'en6', None):
                parts = policy.render(rows, interface)
                route = 'Physical-direct' if interface else 'REJECT'
                rules = parts['rules']
                idx = rules.index('DOMAIN-SUFFIX,example.com,' + route)
                self.assertIn('NETWORK,UDP,REJECT', rules[:idx])
                self.assertIn('RULE-SET,residential-domains,Residual-egress', rules[:idx])
                self.assertNotIn('DOMAIN-SUFFIX,example.com,DIRECT', rules)
                self.assertEqual(parts['proxies'][len(cfg['proxies']):], [
                    {'name': route, 'type': 'direct', 'interface-name': interface, 'udp': False}
                ] if interface else [])
                defaults = policy.render([], interface)
                self.assertEqual(defaults['proxies'], parts['proxies'])
                self.assertEqual(defaults['rules'][-4:], ['DOMAIN-SUFFIX,cn,' + route,
                    'AND,((DOMAIN-REGEX,.+),(RULE-SET,cn-networks)),' + route, *cfg['rules'][-2:]])
                self.assertLess(idx, rules.index('DOMAIN-SUFFIX,cn,' + route))

    def test_rule_injection_and_ip_rejected(self):
        for value in ('foo,MATCH,DIRECT', 'a/b', 'https://example.com', '1.1.1.1', '*.x', '-bad', 'a..b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                domain_policy.rule('exact', value, 'Day8-native')
        with self.assertRaises(ValueError):
            domain_policy.rule('keyword', 'safe', 'GLOBAL')


class JournalTests(unittest.TestCase):
    def test_china_ip_rule_in_connection_log(self):
        journal = monitor.DomainJournal()
        matched = 'And(((DomainRegex,.+) && (RuleSet,cn-networks)))'
        journal.append(f'[TCP] 127.0.0.1:19000 --> cdn.test:443 match {matched} using Physical-direct', [])
        entry, = journal.search('cdn.test')['events']
        self.assertEqual((entry['route'], entry['matched']), ('DIRECT', matched))

    def test_dial_error_does_not_store_raw_diagnostics(self):
        journal = monitor.DomainJournal()
        journal.append('[TCP] dial Day8-native (match Match/) 127.0.0.1:2000 --> failed.test:443 error: private-detail', [])
        event, = journal.search('Failed')['events']
        self.assertEqual(event['outcome'], 'failed')
        self.assertNotIn('private-detail', str(event))

    def test_short_connections_filter_bounded_and_redacted(self):
        journal = monitor.DomainJournal(2)
        def emit(host):
            journal.append(f'info msg="[TCP] 127.0.0.1:30000 --> {host}:443 match Domain(test) using Residual-egress"', ['secret.test'])
        for host in ['expired.test', 'a.one.test', 'secret.test', 'b.one.test']:
            emit(host)
        for query in ['OnE', '*.one.test', '?.one.test']:
            rows = journal.search(query)['events']
            self.assertEqual([r['host'] for r in rows], ['b.one.test', 'a.one.test'])
            self.assertEqual(rows[0]['matched'], 'Domain(test)')
        for query in ('secret', 'expired', '[ab].one.test'):
            self.assertFalse(journal.search(query)['events'])
