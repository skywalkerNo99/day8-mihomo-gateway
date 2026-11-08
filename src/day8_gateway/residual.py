"""Edit the local residential second hop without resealing the enrolled base vault."""
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import node_updates, vault


def settings(payload):
    return {'residual': dict(payload['residual']), 'expected_exit_ip': payload['expected_exit_ip']}


def revision(payload, key):
    return hmac.new(key, b'residual-edit-v1:' + vault.canonical(settings(payload)), hashlib.sha256).hexdigest()


def form(payload, key):
    proxy = payload['residual']
    return {'ok': True, 'revision': revision(payload, key), 'residual': {
        'server': proxy['server'], 'port': proxy['port'], 'username': proxy['username'],
        'expectedExit': payload['expected_exit_ip']}}


def candidate(payload, key, edit):
    if edit.get('revision') != revision(payload, key):
        raise ValueError('配置已经变更，请重新打开编辑窗口。')
    old = payload['residual']
    host = edit['server'].strip().removesuffix('.')
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            host = host.encode('idna').decode('ascii').lower()
        except UnicodeError:
            host = ''
        if len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', s)
                                      for s in host.split('.')):
            raise ValueError('请输入有效的 SOCKS5 主机名或 IP。') from None
    port = edit['port']
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('端口范围为 1 到 65535。')
    try:
        expected = str(ipaddress.ip_address(edit['expectedExit'].strip()))
    except ValueError:
        raise ValueError('请输入有效的预期出口 IP。') from None
    password = edit['password']
    if not password and (host, port) != (old['server'], old['port']):
        raise ValueError('更换接入地址或端口时须重新输入代理密码。')
    proxy = {'server': host, 'port': port, 'username': edit['username'], 'password': password or old['password']}
    if not all(isinstance(proxy[k], str) and 1 <= len(proxy[k].encode()) <= 255 for k in ('username','password')):
        raise ValueError('SOCKS5 用户名和密码须为 1 到 255 字节。')
    return {**payload, 'residual': proxy, 'expected_exit_ip': expected}


def aad(profile):
    return b'day8-residual-v1:' + hashlib.sha256(profile.read_bytes()).digest()


def save(root, profile, key, payload):
    nonce = os.urandom(12)
    data = AESGCM(key).encrypt(nonce, vault.canonical(settings(payload)), aad(profile))
    node_updates.save(root / '.local/residual.enc', base64.b64encode(nonce + data).decode('ascii'))


def load(root, profile, key, payload):
    path = root / '.local/residual.enc'
    if path.exists():
        blob = base64.b64decode(path.read_bytes(), validate=True)
        values = json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], aad(profile)))
        payload.update(residual=values['residual'], expected_exit_ip=values['expected_exit_ip'])


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise OSError('SOCKS5 disconnected')
        data.extend(part)
    return bytes(data)


def connect_target(sock, host, port):
    try:
        addr = ipaddress.ip_address(host)
        address = bytes([1 if addr.version == 4 else 4]) + addr.packed
    except ValueError:
        name = host.encode('idna')
        address = bytes([3, len(name)]) + name
    sock.sendall(b'\x05\x01\x00' + address + struct.pack('!H', port))
    version, result, reserved, atyp = read_exact(sock, 4)
    if (version, result, reserved) != (5, 0, 0) or atyp not in (1, 3, 4):
        raise OSError('SOCKS5 connection failed')
    length = read_exact(sock, 1)[0] if atyp == 3 else {1: 4, 4: 16}[atyp]
    read_exact(sock, length + 2)


def test_exit(payload, adapter_port):
    """Exercise the candidate over the active Day8 adapter before changing any live routes."""
    proxy = payload['residual']
    with socket.create_connection(('127.0.0.1', adapter_port), timeout=12) as sock:
        sock.sendall(b'\x05\x01\x00')
        if read_exact(sock, 2) != b'\x05\x00':
            return False
        connect_target(sock, proxy['server'], proxy['port'])
        sock.sendall(b'\x05\x01\x02')
        if read_exact(sock, 2) != b'\x05\x02':
            return False
        user, password = (proxy[k].encode() for k in ('username', 'password'))
        sock.sendall(bytes([1, len(user)]) + user + bytes([len(password)]) + password)
        if read_exact(sock, 2) != b'\x01\x00':
            return False
        connect_target(sock, 'chatgpt.com', 443)
        conn = http.client.HTTPSConnection('chatgpt.com', timeout=12)
        try:
            conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname='chatgpt.com')
            conn.request('GET', '/cdn-cgi/trace')
            response = conn.getresponse()
            fields = dict(s.split('=', 1) for s in response.read(32768).decode().splitlines() if '=' in s)
            return response.status == 200 and fields.get('h') == 'chatgpt.com' and fields.get('ip') == payload['expected_exit_ip']
        finally:
            conn.close()
