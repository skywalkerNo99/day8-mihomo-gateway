"""Local TCP routing overrides for the macOS single-app rule core."""
import base64
import hashlib
import ipaddress
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import config, node_updates

KINDS = {'exact': 'DOMAIN', 'suffix': 'DOMAIN-SUFFIX', 'keyword': 'DOMAIN-KEYWORD'}
TARGETS = (config.TRANSPORT, config.EGRESS, 'DIRECT', 'REJECT')
AAD = b'day8-local-domain-policy-v1'
LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')


def rule(kind, value, target):
    if kind not in KINDS or target not in TARGETS or not isinstance(value, str):
        raise ValueError('规则类型或线路无效')
    value = value.strip().lower().removesuffix('.')
    if kind == 'suffix':
        value = value.removeprefix('*.').removeprefix('+.')
    if kind != 'keyword':
        try:
            value = value.encode('idna').decode('ascii')
        except UnicodeError:
            raise ValueError('域名格式无效') from None
    if not value or len(value) > 253 or not all(LABEL.fullmatch(part) for part in value.split('.')):
        raise ValueError('请输入域名或关键词，不含 URL、端口或路径')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return {'kind': kind, 'value': value, 'target': target}
    raise ValueError('规则需要域名，不能使用 IP 地址')


class Policy:
    def __init__(self, cfg, path, key):
        self.path = path
        self.key = key
        self.base = list(cfg['rules'])
        self.proxies = list(cfg['proxies'])
        self.builtin = cfg['rule-providers']['residential-domains']['payload']
        self.rows = []
        if path.exists():
            raw = base64.b64decode(path.read_bytes(), validate=True)
            rows = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], AAD))
            self.rows = [rule(**r) for r in rows]

    def snapshot(self):
        revision = hashlib.sha256(json.dumps(self.rows, sort_keys=True).encode()).hexdigest()
        return {'ok': True, 'domainRules': self.rows, 'revision': revision, 'protectedDomains': self.builtin}

    def change(self, message):
        if message['revision'] != self.snapshot()['revision']:
            raise ValueError('规则列表已变更，请重新载入')
        rows = list(self.rows)
        idx = message.get('index')
        if type(idx) is not int or not -1 <= idx < len(rows):
            raise ValueError('规则已不存在')
        if message['action'] == 'delete' and idx >= 0:
            rows.pop(idx)
        elif message['action'] in ('up', 'down') and idx >= 0:
            dest = idx + (-1 if message['action'] == 'up' else 1)
            if not 0 <= dest < len(rows):
                raise ValueError('规则已在边界')
            rows.insert(dest, rows.pop(idx))
        elif message['action'] == 'save':
            item = rule(**message['rule'])
            if any(i != idx and (r['kind'], r['value']) == (item['kind'], item['value']) for i, r in enumerate(rows)):
                raise ValueError('相同的匹配规则已存在')
            if idx < 0:
                if len(rows) >= 500:
                    raise ValueError('最多支持 500 条自定义规则')
                rows.append(item)
            else:
                rows[idx] = item
        else:
            raise ValueError('无效修改')
        return rows

    def ordinary_probe(self):
        # The system check needs a public target still assigned to the default route.
        for host in ('example.com', 'example.org', 'www.iana.org', 'www.wikipedia.org'):
            for row in self.rows:
                value, kind = row['value'], row['kind']
                if ((kind == 'exact' and host == value)
                    or (kind == 'suffix' and (host == value or host.endswith('.' + value)))
                    or (kind == 'keyword' and value in host)):
                    if row['target'] != config.TRANSPORT:
                        break
                    return host
            else:
                return host
        return None

    def render(self, rows, interface):
        proxies = list(self.proxies)
        if interface:
            proxies.append({'name': 'Physical-direct', 'type': 'direct', 'udp': False,
                            'interface-name': interface})
        return {'proxies': proxies, 'rules': self.rules(rows, interface)}

    def rules(self, rows, interface=None):
        # Built-in residential rules and UDP rejection always precede local overrides.
        result = list(self.base[:-2])
        for row in rows:
            prefix = KINDS[row['kind']] + ',' + row['value'] + ','
            target = row['target']
            if target == 'DIRECT':
                target = 'Physical-direct' if interface else 'REJECT'
            result.append(prefix + target)
            if target != 'REJECT':
                result.append(prefix + 'REJECT')
        direct = 'Physical-direct' if interface else 'REJECT'
        for predicate in ('DOMAIN-SUFFIX,cn', 'AND,((DOMAIN-REGEX,.+),(RULE-SET,cn-networks))'):
            result.append(predicate + ',' + direct)
        return result + self.base[-2:]

    def persist(self, rows):
        nonce = os.urandom(12)
        blob = AESGCM(self.key).encrypt(nonce, json.dumps(rows).encode(), AAD)
        node_updates.save(self.path, base64.b64encode(nonce + blob).decode())
        self.rows = rows
