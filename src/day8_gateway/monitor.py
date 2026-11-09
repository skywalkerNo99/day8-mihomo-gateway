"""Health of two explicit outbounds and bounded, credential-free connection logs."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from fnmatch import translate
from time import time
import re
from urllib.parse import urlencode

from . import config, controller

PROBE = 'https://www.gstatic.com/generate_204'
LOG_TIMEZONE = timezone(timedelta(hours=8))


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
    match = re.search(r'\[(TCP|UDP)\]\s+\S+\s+-->\s+([a-zA-Z0-9.:_\[\]-]{1,270})\s+.*?\busing\s+(Day8-native|Residual-egress|REJECT|DIRECT|Physical-direct)\b', line)
    if match:
        network, target, route = match.groups()
        if any(host.lower() in target.lower() for host in hidden):
            target = '<upstream>'
        if route == 'Physical-direct':
            route = 'DIRECT'
        return f'[{network}] {target} via {route}'


def timestamped_connection_line(line, hidden):
    entry = connection_line(line, hidden)
    if entry and '<upstream>' not in entry:
        return f'{datetime.now(LOG_TIMEZONE).isoformat(timespec="milliseconds")} {entry}'


class DomainJournal:
    """Bounded in-memory stream of routing decisions. Store only allowlisted fields."""
    def __init__(self, capacity=1500):
        self.entries = deque(maxlen=capacity)
        self.sequence = 0

    def append(self, line, hidden):
        entry = connection_line(line, hidden)
        outcome = 'routed'
        if entry:
            if '<upstream>' in entry:
                return
            network, target, _, route = entry.split()
            matched = re.search(r'\bmatch ([\w.(),:+/ &|-]{1,300}) using ', line, re.IGNORECASE)
            rule_name = matched[1] if matched else None
        else:
            # Dial errors may contain proxy endpoints and diagnostics. Retain only safe fields.
            failure = re.search(r'\[(TCP|UDP)\] dial (Day8-native|Residual-egress|REJECT|Physical-direct) '
                                r'\(match ([\w.(),:+/ &|-]{1,300})\) \S+ --> ([a-zA-Z0-9.:_\[\]-]{1,270}) ', line)
            if not failure:
                return
            protocol, route, rule_name, target = failure.groups()
            if any(h.lower() in target.lower() for h in hidden):
                return
            if route == 'Physical-direct':
                route = 'DIRECT'
            network = '[' + protocol + ']'
            outcome = 'failed'
        host, sep, port = target.rpartition(':')
        if not sep or not port.isdigit():
            return
        host = host.strip('[]').lower()
        self.sequence += 1
        self.entries.append({'id': self.sequence, 'at': time(), 'host': host, 'port': int(port),
                             'network': network[1:-1], 'route': route, 'matched': rule_name,
                             'outcome': 'rejected' if route == 'REJECT' else outcome})

    def search(self, query):
        query = query.strip().lower()
        if len(query) > 253:
            raise ValueError('查询过长')
        # Literal substring by default. Only * and ? are special in wildcard mode.
        if '*' in query or '?' in query:
            pattern = re.compile(translate(query.replace('[', '[[]')))
            matches = pattern.fullmatch
        else:
            matches = lambda host: query in host
        return {'ok': True, 'events': [e for e in reversed(self.entries) if matches(e['host'])]}
