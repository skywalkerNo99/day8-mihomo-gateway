"""Exercise the system route directly, correlate fresh routed connections, verify the exit."""
import concurrent.futures
import http.client
import re
import socket
import ssl
import subprocess
import time

from . import controller


def interface(address):
    family = ['-inet6'] if ':' in address else []
    text = subprocess.check_output(['/sbin/route', '-n', 'get', *family, address], text=True)
    match = re.search(r'interface:\s*(\S+)', text)
    return match.group(1) if match else ''


def check(url, secret, expected):
    def connections():
        return controller.request(url, secret, '/connections').get('connections') or []

    def probe(pair):
        host, target = pair
        conn = http.client.HTTPSConnection(host, timeout=12, context=ssl.create_default_context())
        try:
            before = {c['id'] for c in connections()}
            conn.connect()
            peer = conn.sock.getpeername()[0]
            tunnel = interface(peer)
            matched = False
            for _ in range(8):
                matched = any(c['id'] not in before and c['metadata'].get('host', '').lower() == host
                              and c['metadata'].get('inboundName') == 'system-tun'
                              and target in c.get('chains', []) for c in connections())
                if matched:
                    break
                time.sleep(.1)
            trace = host == 'chatgpt.com'
            path = {'chatgpt.com': '/cdn-cgi/trace', 'api.openai.com': '/v1/models'}.get(host, '/')
            conn.request('GET' if path != '/' else 'HEAD', path)
            response = conn.getresponse()
            exit_ok = None
            if trace:
                fields = dict(s.split('=', 1) for s in response.read(32768).decode().splitlines() if '=' in s)
                exit_ok = fields.get('h') == host and fields.get('ip') == expected
            return {'host': host, 'interface': tunnel, 'chain': target if matched else None,
                    'http': response.status, 'exitMatches': exit_ok,
                    'ok': matched and tunnel == 'utun63' and exit_ok is not False}
        except (OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError):
            return {'host': host, 'ok': False}
        finally:
            conn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(probe, [('example.com', 'Day8-native'), ('www.apple.com', 'Residual-egress'),
                                     ('api.openai.com', 'Residual-egress'), ('chatgpt.com', 'Residual-egress')]))
    return {'ok': all(r['ok'] for r in rows), 'routes': rows}
