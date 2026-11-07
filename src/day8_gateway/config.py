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

def bootstrap(payload):
    server=payload['day8']['server']
    try:
        ip=ipaddress.ip_address(server)
        return f"IP-CIDR{'6'if ip.version==6 else ''},{ip}/{ip.max_prefixlen},DIRECT,no-resolve"
    except ValueError:return f'DOMAIN,{server},DIRECT'

def nodes(payload,adapter_port):
    res=payload['residual']
    return [{'name':TRANSPORT,'type':'socks5','server':'127.0.0.1','port':adapter_port,'udp':False},
            {'name':EGRESS,'type':'socks5',**res,'udp':False,'dialer-proxy':TRANSPORT}]

def native(payload,port=17890,adapter_port=17889,controller_port=17891,tun=False,bind='127.0.0.1'):
    validate(payload)
    private=['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,10.0.0.0/8,DIRECT,no-resolve','IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve']if tun else []
    return {'mixed-port':port,'allow-lan':bind!='127.0.0.1','bind-address':bind,'mode':'rule','log-level':'warning','ipv6':False,
            'external-controller':f'127.0.0.1:{controller_port}','secret':secrets.token_hex(24),
            'tun':{'enable':tun,'stack':'system','auto-route':True,'auto-detect-interface':True,'dns-hijack':['any:53','tcp://any:53']},
            'dns':{'enable':True,'enhanced-mode':'fake-ip','ipv6':False,'default-nameserver':['1.1.1.1'],'nameserver':[f'https://1.1.1.1/dns-query#{EGRESS}']},
            'proxies':nodes(payload,adapter_port),'rules':[bootstrap(payload),*private,'NETWORK,UDP,REJECT',f'MATCH,{EGRESS}']}

def macos(payload,original,rules_dir,adapter_port=17889,strict=False):
    validate(payload);cfg=copy.deepcopy(original)
    cfg['proxies']=[p for p in cfg.get('proxies',[])if p['name']not in [TRANSPORT,EGRESS]]+nodes(payload,adapter_port)
    providers={}
    for name,file in [('day8-apple','apple.yaml'),('day8-openai','openai.yaml'),('day8-dependencies','official-additions.yaml')]:
        providers[name]={'type':'inline','behavior':'domain','payload':list(dict.fromkeys(yaml.safe_load((Path(rules_dir)/file).read_text())['payload']))}
    providers['day8-apple-ips']={'type':'inline','behavior':'ipcidr','payload':APPLE_IPS}
    cfg.setdefault('rule-providers',{}).update(providers)
    prefix=[bootstrap(payload)]+[f'AND,((NETWORK,UDP),(RULE-SET,{name})),REJECT'for name in providers]
    prefix += [f'RULE-SET,{name},{EGRESS}'+(',no-resolve'if name=='day8-apple-ips'else '')for name in providers]
    if strict:
        cfg['rules']=[bootstrap(payload),'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,10.0.0.0/8,DIRECT,no-resolve','IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve','NETWORK,UDP,REJECT',f'MATCH,{EGRESS}']
    else:
        cfg['rules']=prefix+[r for r in cfg.get('rules',[])if 'RULE-SET,day8-'not in r and r not in prefix]
    cfg['mode']='rule'
    cfg.setdefault('dns',{}).update({'enable':True,'enhanced-mode':'fake-ip','ipv6':False})
    cfg.setdefault('sniffer',{}).update({'enable':True,'parse-pure-ip':True,'sniff':{'TLS':{'ports':[443,8443]},'HTTP':{'ports':[80,'8080-8880']},'QUIC':{'ports':[443,8443]}}})
    return cfg
