"""User-side routing for the fixed macOS TUN capture service."""
import asyncio
import hashlib
import hmac
import ipaddress
import time

from . import catalog, config

PORT = 17890
CONTROLLER = 17891
HANDOFF = 17892


def link_key(key):
    return hmac.new(key, b'day8-macos-local-capture-v1', hashlib.sha256).hexdigest()


def routing(payload, key, adapter_port):
    cfg = config.native(payload, PORT, adapter_port, CONTROLLER)
    cfg['log-level'] = 'info'
    cfg['rule-providers']['cn-networks'] = config.mainland_provider()
    cfg['listeners'] = [{'name': 'system-tun', 'type': 'shadowsocks',
                         'listen': '127.0.0.1', 'port': HANDOFF, 'udp': False,
                         'cipher': 'aes-128-gcm', 'password': link_key(key)}]
    return cfg


class Uplink:
    """Scope only the Muon transport to the physical uplink to avoid TUN recursion."""
    def __init__(self):
        self.index = None
        self.until = 0
        self.lock = asyncio.Lock()

    async def __call__(self, host, port):
        ipaddress.ip_address(host)  # Do not send bootstrap DNS through this same tunnel.
        async with self.lock:
            if not self.index or time.monotonic() >= self.until:
                self.index = await asyncio.to_thread(catalog.uplink_index)
                self.until = time.monotonic() + 3
            index = self.index
        if not index:
            raise OSError('Physical uplink unavailable.')
        return await catalog.dial_probe(host, port, index)
