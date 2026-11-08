"""Health of two explicit outbounds and bounded, credential-free connection logs."""
from concurrent.futures import ThreadPoolExecutor
import re
from urllib.parse import urlencode

from . import config, controller

PROBE = 'https://www.gstatic.com/generate_204'


def snapshot(url, secret):
    def probe(name):
        try:
            query = urlencode({'url': PROBE, 'timeout': 5000})
            result = controller.request(url, secret, f'/proxies/{name}/delay?{query}')
            delay = result['delay']
            if type(delay) is int and delay >= 0:
                return {'online': True, 'ms': delay}
        except (OSError, ValueError, KeyError):
            pass
        return {'online': False, 'ms': None}

    names = [config.TRANSPORT, config.EGRESS]
    with ThreadPoolExecutor(max_workers=2) as pool:
        nodes = dict(zip(names, pool.map(probe, names)))
    table = controller.request(url, secret, '/connections')
    return {'ok': True, 'nodes': nodes, 'connections': len(table.get('connections') or []),
            'upload': table.get('uploadTotal', 0), 'download': table.get('downloadTotal', 0)}


def connection_line(line, hidden):
    match = re.search(r'\[(TCP|UDP)\]\s+\S+\s+-->\s+([a-zA-Z0-9.:_\[\]-]{1,270})\s+.*?\busing\s+(Day8-native|Residual-egress|REJECT|DIRECT)\b', line)
    if match:
        network, target, route = match.groups()
        if any(host.lower() in target.lower() for host in hidden):
            target = '<upstream>'
        return f'[{network}] {target} via {route}'
