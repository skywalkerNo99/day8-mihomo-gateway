"""DNS interception and a public-UDP guard in a dedicated Linux container namespace."""
import os
from pathlib import Path
import subprocess
import sys

from . import config


def configure(cfg):
    cfg['tun'].update(enable=True, device='day8tun0')
    cfg['dns']['listen'] = '127.0.0.1:1053'
    cfg['rule-providers']['cn-networks'] = config.mainland_provider()
    cfg['rules'][-2:-2] = config.mainland_rules('DIRECT')


def execute(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError('Container firewall setup failed.')


def ensure_rule(table, rule):
    command = ['iptables', '-w', '5', '-t', table]
    result = subprocess.run(command + ['-C', 'OUTPUT'] + rule, capture_output=True)
    if result.returncode:
        execute(command + ['-I', 'OUTPUT', '1'] + rule)


def prepare():
    if sys.platform != 'linux' or os.geteuid() != 0 or not Path('/.dockerenv').is_file():
        raise RuntimeError('Container TUN requires a dedicated root Docker container on Linux.')
    if not Path('/dev/net/tun').is_char_device():
        raise RuntimeError('Container TUN device is missing.')
    if Path('/proc/sys/net/ipv6/conf/all/disable_ipv6').read_text().strip() != '1':
        raise RuntimeError('Disable IPv6 in this container namespace before starting container TUN.')

    # Restore only our chain atomically. Keep Docker's DNS and unrelated firewall rules.
    rules = ['*filter', ':DAY8_UDP - [0:0]', '-F DAY8_UDP', '-A DAY8_UDP -o lo -j RETURN']
    local = [net for net in config.LOCAL_NETS if ':' not in net]
    for subnet in [*local, '224.0.0.0/4', '255.255.255.255/32']:
        rules.append(f'-A DAY8_UDP -d {subnet} -j RETURN')
    rules.extend(['-A DAY8_UDP -j REJECT --reject-with icmp-port-unreachable', 'COMMIT', ''])
    execute(['iptables-restore', '-w', '5', '--noflush'], input='\n'.join(rules))
    ensure_rule('filter', ['-p', 'udp', '-j', 'DAY8_UDP'])
    for protocol in ('udp', 'tcp'):
        ensure_rule('nat', ['-p', protocol, '--dport', '53', '-j', 'REDIRECT', '--to-ports', '1053'])
