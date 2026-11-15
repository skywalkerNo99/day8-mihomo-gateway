"""Opt-in LAN access to the existing macOS routing core; no upstream secrets."""
import ipaddress
import json
import os

PRIVATE = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def network(value):
    if not isinstance(value, str):
        raise ValueError('Specify one private IPv4 LAN subnet.')
    subnet = ipaddress.ip_network(value)
    if (subnet.version != 4 or subnet.prefixlen < 16
            or not any(subnet.subnet_of(parent) for parent in PRIVATE)):
        raise ValueError('LAN access requires a private IPv4 subnet with prefix /16 or narrower.')
    return str(subnet)


def load(path):
    if not path.is_file():
        return None
    return network(json.loads(path.read_text())['network'])


def fields(value):
    return {'allow-lan': value is not None,
            'bind-address': '0.0.0.0' if value is not None else '127.0.0.1',
            'lan-allowed-ips': ['127.0.0.0/8'] + ([network(value)] if value is not None else []),
            'lan-disallowed-ips': []}


def save(path, value):
    if value is None:
        path.unlink(missing_ok=True)
        return
    data = json.dumps({'network': network(value)}).encode()
    temporary = path.with_suffix('.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def status(cfg):
    return {'ok': True, 'enabled': cfg['allow-lan'], 'port': cfg['mixed-port'],
            'allowedNetworks': cfg.get('lan-allowed-ips', ['127.0.0.0/8'])}
