"""Names and selectable Muon profiles from an authenticated local catalog."""
import hashlib
import json
import hmac
import re
import secrets
import asyncio
import subprocess
import socket
import ipaddress
import sys
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import adapter
from pathlib import Path

from . import node_updates, vault

FILENAME = 'routes.enc.json'


def token(key, node):
    normalized = {k: node[k] for k in node_updates.FIELDS if k in node}
    normalized.setdefault('max_padding', 1024)
    return hmac.new(key, b'day8-active-route-v1\0' + vault.canonical(normalized), hashlib.sha256).hexdigest()


def from_export(response, base):
    entries = []
    seen = set()
    for item in response['servers']:
        symbol = item['symbol']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', symbol) or symbol in seen:
            raise ValueError('Invalid or duplicate route identifier.')
        seen.add(symbol)
        name = item['name']
        if not isinstance(name, str) or not name.isprintable() or not 1 <= len(name) <= 100:
            raise ValueError('Invalid route name.')
        node = None
        if item['protocol'] == 'muon' and item['encryption_method'] in ('aes-128-gcm', 'aes-256-gcm'):
            node = node_updates.validate_node(base, {
                'server': item['host'], 'port': item['port'], 'cipher': item['encryption_method'],
                'server_password': item['encryption_password'], 'user_password': response['credentials']['password'],
                'max_padding': item.get('max_padding', 1024)})
        entries.append({'id': symbol, 'name': name, 'node': node, 'maintenance': item.get('is_under_maintenance', False)})
    if not 0 < len(entries) <= 512:
        raise ValueError('Invalid catalog size.')
    return entries


def save_export(source, profile, target, key, base):
    if source.stat().st_size > vault.MAX_BYTES:
        raise ValueError('Oversized route export.')
    entries = from_export(json.loads(source.read_bytes()), base)
    original = vault.read_envelope(profile)
    header = {k: v for k, v in original.items() if k != 'ciphertext'}
    nonce = secrets.token_bytes(12)
    header['nonce'] = vault.b64(nonce)
    data = vault.canonical({'kind': 'day8-routes-v1', 'routes': entries})
    header['ciphertext'] = vault.b64(AESGCM(key).encrypt(nonce, data, vault.canonical(header)))
    node_updates.save(target, json.dumps(header))
    return len(entries)


def uplink_index():
    if sys.platform != 'darwin':
        return None
    report = subprocess.run(['/usr/sbin/scutil', '--nwi'], capture_output=True, text=True, timeout=3)
    for name in re.findall(r'^\s*([a-z]+[0-9]+) : flags', report.stdout, re.MULTILINE):
        if name.startswith(('en', 'bridge', 'pdp_ip')):
            return socket.if_nametoindex(name)
    return None


async def dial_probe(host, port, interface):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return await asyncio.open_connection(host, port)
    if not interface:
        return await asyncio.open_connection(host, port)
    family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
    conn = socket.socket(family, socket.SOCK_STREAM)
    try:
        # Darwin's scoped socket options limit only this candidate's transport.
        level, option = (socket.IPPROTO_IP, 25) if ip.version == 4 else (socket.IPPROTO_IPV6, 125)
        conn.setsockopt(level, option, interface)
        conn.setblocking(False)
        await asyncio.get_running_loop().sock_connect(conn, (host, port))
        return await asyncio.open_connection(sock=conn)
    except BaseException:
        conn.close()
        raise


async def probe(entry, interface):
    async def dial(host, port):
        return await dial_probe(host, port, interface)

    muon = adapter.Muon(entry['node'], dial=dial)
    clients = {}

    def connected(reader, writer):
        task = asyncio.create_task(adapter.handle(reader, writer, muon))
        clients[task] = writer
        task.add_done_callback(lambda done: clients.pop(done, None))

    listener = await asyncio.start_server(connected, '127.0.0.1', 0)
    port = listener.sockets[0].getsockname()[1]
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            '/usr/bin/curl', '-q', '--silent', '--max-time', '8', '--noproxy', '',
            '--proxy', f'socks5h://127.0.0.1:{port}', '--output', '/dev/null',
            '--write-out', '%{http_code}', 'https://www.gstatic.com/generate_204',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            code, _ = await asyncio.wait_for(proc.communicate(), 10)
        except (TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode == 0 and code == b'204':
            return round((time.monotonic() - start) * 1000)
        return -1
    except (OSError, TimeoutError):
        return -1
    finally:
        listener.close()
        for task, writer in list(clients.items()):
            writer.transport.abort()
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)


async def probe_routes(entries):
    limit = asyncio.Semaphore(4)
    interface = uplink_index()

    async def measure(entry):
        async with limit:
            return entry['id'], await probe(entry, interface)

    return dict(await asyncio.gather(*(measure(e) for e in entries if e['node'] and not e['maintenance'])))


def read(root, key):
    path = Path(root) / 'routes.local.enc.json'
    if not path.is_file():
        path = Path(root) / FILENAME
    if not path.is_file():
        return []
    data = vault.decrypt_with_key(vault.read_envelope(path), key)
    if data.get('kind') != 'day8-routes-v1':
        raise ValueError('Invalid route catalog.')
    return data['routes']


def summary(entries, key, active):
    rows = [{'id': e['id'], 'name': e['name'], 'available': bool(e['node']) and not e['maintenance']}
            for e in entries]
    matches = [e for e in entries if e['node'] and active and hmac.compare_digest(token(key, e['node']), active)]
    selected = matches[0] if len(matches) == 1 else {}
    return {'routes': rows, 'activeID': selected.get('id'), 'activeName': selected.get('name')}
