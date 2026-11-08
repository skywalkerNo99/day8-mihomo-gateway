"""Build native Mihomo configs from an unlocked profile, entirely in memory."""
import copy
import ipaddress
import json
from pathlib import Path
import secrets
import yaml

TRANSPORT='Day8-native'
EGRESS='Residual-egress'
APPLE_IPS=['17.0.0.0/8','2403:300::/32','2620:149::/32','2a01:b740::/32']

def validate(payload):
    if not isinstance(payload,dict)or payload.get('schema')!=1:raise ValueError('Unsupported deployment profile schema.')
    day8=payload['day8'];res=payload['residual']
    if not isinstance(day8,dict)or not isinstance(res,dict):raise ValueError('Invalid deployment profile sections.')
    if day8['cipher']not in ['aes-128-gcm','aes-256-gcm']:raise ValueError('Unsupported Muon cipher.')
    for section,fields in [(day8,['server','server_password','user_password']),(res,['server','username','password'])]:
        for field in fields:
            if not isinstance(section.get(field),str)or not section[field]:raise ValueError('Missing deployment credential field.')
        if type(section.get('port'))is not int or not 1<=section['port']<=65535:raise ValueError('Invalid server port.')
    if not isinstance(payload.get('expected_exit_ip'),str):raise ValueError('expected_exit_ip is required.')
    ipaddress.ip_address(payload['expected_exit_ip'])
    if type(day8.get('max_padding',1024))is not int or not 0<=day8.get('max_padding',1024)<=4096:raise ValueError('Invalid padding limit.')
    return payload

def import_legacy(directory):
    directory=Path(directory)
    day8=json.loads((directory/'muon.json').read_text())
    cfg=json.loads((directory/'mihomo.json').read_text())
    res=next(p for p in cfg['proxies']if p.get('dialer-proxy')and p['type']=='socks5')
    return validate({'schema':1,'day8':{k:day8[k]for k in ['server','port','cipher','server_password','user_password','max_padding']if k in day8},
                     'residual':{k:res[k]for k in ['server','port','username','password']},'expected_exit_ip':res['server']})

RULES_DIR = Path(__file__).resolve().parents[2] / 'rules'
LOCAL_NETS = ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
              '169.254.0.0/16', '::1/128', 'fc00::/7', 'fe80::/10')


def bootstrap(payload):
    day8 = payload['day8']
    try:
        ip = ipaddress.ip_address(day8['server'])
        target = f"IP-CIDR{'6' if ip.version == 6 else ''},{ip}/{ip.max_prefixlen},no-resolve"
    except ValueError:
        target = f"DOMAIN,{day8['server']}"
    return f"AND,((NETWORK,TCP),(DST-PORT,{day8['port']}),({target})),DIRECT"


def nodes(payload, adapter_port):
    res = payload['residual']
    return [
        {'name': TRANSPORT, 'type': 'socks5', 'server': '127.0.0.1', 'port': adapter_port, 'udp': False},
        {'name': EGRESS, 'type': 'socks5', **{k: res[k] for k in ('server', 'port', 'username', 'password')},
         'udp': False, 'dialer-proxy': TRANSPORT},
    ]


def apply_routes(cfg, payload, adapter_port, rules_dir, strict=False):
    has_tun = (cfg.get('tun') or {}).get('enable')
    rules = []
    if has_tun:
        rules.append(bootstrap(payload))
        for subnet in LOCAL_NETS:
            kind = 'IP-CIDR6' if ':' in subnet else 'IP-CIDR'
            rules.append(f'{kind},{subnet},DIRECT,no-resolve')

    cfg['proxies'] = nodes(payload, adapter_port)
    for key in ('proxy-providers', 'proxy-groups', 'sub-rules', 'listeners', 'tunnels'):
        cfg.pop(key, None)
    domains = yaml.safe_load((Path(rules_dir) / 'residential-domains.yaml').read_text())['payload']
    cfg['rule-providers'] = {
        'residential-domains': {'type': 'inline', 'behavior': 'domain', 'payload': domains},
        'apple-ips': {'type': 'inline', 'behavior': 'ipcidr', 'payload': APPLE_IPS},
    }
    rules.append('NETWORK,UDP,REJECT')
    if not strict:
        for name in cfg['rule-providers']:
            suffix = ',no-resolve' if name == 'apple-ips' else ''
            rules.extend(f'RULE-SET,{name},{out}{suffix}' for out in (EGRESS, 'REJECT'))
    cfg['rules'] = [*rules, f'MATCH,{EGRESS if strict else TRANSPORT}', 'MATCH,REJECT']
    cfg['mode'] = 'rule'
    # Retain the GUI's DNS listener, retire resolver overrides from the old subscription.
    dns = cfg.get('dns') or {}
    cfg['dns'] = {k: dns[k] for k in ('listen', 'fake-ip-range') if k in dns}
    cfg['dns'].update({'enable': True, 'enhanced-mode': 'fake-ip', 'ipv6': False,
                       'default-nameserver': ['1.1.1.1'],
                       'nameserver': [f'https://1.1.1.1/dns-query#{TRANSPORT}']})
    cfg['sniffer'] = {'enable': True, 'parse-pure-ip': True, 'sniff': {
        'TLS': {'ports': [443, 8443]}, 'HTTP': {'ports': [80, '8080-8880']},
        'QUIC': {'ports': [443, 8443]}}}
    return cfg


def native(payload, port=17890, adapter_port=17889, controller_port=17891, tun=False, bind='127.0.0.1'):
    validate(payload)
    cfg = {'mixed-port': port, 'allow-lan': bind != '127.0.0.1', 'bind-address': bind,
           'log-level': 'warning', 'ipv6': False, 'external-controller': f'127.0.0.1:{controller_port}',
           'secret': secrets.token_hex(24), 'tun': {'enable': tun, 'stack': 'system', 'auto-route': True,
           'auto-detect-interface': True, 'dns-hijack': ['any:53', 'tcp://any:53']}}
    return apply_routes(cfg, payload, adapter_port, RULES_DIR)


def macos(payload, original, rules_dir, adapter_port=17889, strict=False):
    validate(payload)
    return apply_routes(copy.deepcopy(original), payload, adapter_port, rules_dir, strict)
