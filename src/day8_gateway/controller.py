"""Loopback Mihomo control API. Never log raw configs or controller tokens."""
import json
import urllib.request
from urllib.parse import urlparse


def request(url, secret, route, payload=None, method=None):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('The Mihomo controller must be on loopback HTTP.')
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url.rstrip('/') + route, data=data,
                                method=method or ('POST' if data is not None else 'GET'),
                                headers={'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'})
    # Do not send local control traffic through an inherited HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as response:
        body = response.read(5_000_000)
        return json.loads(body) if body else {}


def apply(url, secret, config):
    return request(url, secret, '/configs?force=true', {'payload': json.dumps(config)}, 'PUT')
