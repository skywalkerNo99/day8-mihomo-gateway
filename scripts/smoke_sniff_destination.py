#!/usr/bin/env python3
"""Verify both real cores preserve IP-selected CDN targets while sniffing for rules."""
import contextlib
import json
from pathlib import Path
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from day8_gateway import controller, domain_policy, single_app
from test_vault_config import fixture
from smoke_routes import address, handshake, OK


def receive_request(reader):
    first = reader.read(1)
    if first == b'G':
        while reader.readline(16384) not in (b'\r\n', b''):
            pass
    elif first == b'\x16':
        header = reader.read(4)
        assert len(header) == 4
        assert len(reader.read(struct.unpack('!H', header[2:])[0])) == struct.unpack('!H', header[2:])[0]
    else:
        raise AssertionError('Expected HTTP request or TLS ClientHello.')


class DirectPeer(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        receive_request(self.rfile)
        self.request.sendall(self.server.marker + b'\n')


class IPv6Peer(socketserver.ThreadingTCPServer):
    address_family = socket.AF_INET6


class RoutePeer(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        handshake(self.rfile, self.request, 0)
        dest = address(self.rfile)
        self.request.sendall(OK)
        two_hops = dest == self.server.residual
        if two_hops:
            handshake(self.rfile, self.request, 2)
            assert self.rfile.read(1) == b'\x01'
            user = self.rfile.read(self.rfile.read(1)[0])
            password = self.rfile.read(self.rfile.read(1)[0])
            assert (user, password) == (b'test-user', b'test-pass')
            self.request.sendall(b'\x01\x00')
            dest = address(self.rfile)
            self.request.sendall(OK)
        receive_request(self.rfile)
        marker = 'two' if two_hops else 'one'
        self.request.sendall(f'{marker} {dest[0]}\n'.encode())


class DNSPeer(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        while size := self.rfile.read(2):
            raw = self.rfile.read(struct.unpack('!H', size)[0])
            offset, parts = 12, []
            while raw[offset]:
                length = raw[offset]
                parts.append(raw[offset + 1:offset + 1 + length].decode())
                offset += length + 1
            kind = struct.unpack('!H', raw[offset + 1:offset + 3])[0]
            self.server.questions.append('.'.join(parts))
            family = socket.AF_INET if self.server.answer == '127.0.0.1' else socket.AF_INET6
            answer_kind = 1 if family == socket.AF_INET else 28
            answer = b''
            if kind == answer_kind:
                address = socket.inet_pton(family, self.server.answer)
                answer = b'\xc0\x0c' + struct.pack('!HHIH', kind, 1, 1, len(address)) + address
            response = raw[:2] + struct.pack('!HHHHH', 0x8180, 1, int(bool(answer)), 0, 0)
            response += raw[12:offset + 5] + answer
            self.request.sendall(struct.pack('!H', len(response)) + response)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start_core(resources, cfg):
    folder = resources.enter_context(tempfile.TemporaryDirectory(prefix='day8-sniff-smoke-'))
    process = subprocess.Popen([str(ROOT / '.tools/mihomo'), '-d', folder, '-f', '/dev/stdin'],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    resources.callback(process.wait, timeout=10)
    resources.callback(process.terminate)
    process.stdin.write(json.dumps(cfg).encode())
    process.stdin.close()
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Isolated core exited before becoming ready.')
        try:
            controller.request('http://' + cfg['external-controller'], cfg['secret'], '/version')
            return
        except OSError:
            time.sleep(.05)
    raise RuntimeError('Isolated core did not become ready.')


def probe(port, target, target_port, host, tls):
    with socket.create_connection(('127.0.0.1', port), 5) as sock, sock.makefile('rb') as reader:
        sock.settimeout(5)
        sock.sendall(b'\x05\x01\x00')
        assert reader.read(2) == b'\x05\x00'
        try:
            address = b'\x01' + socket.inet_aton(target)
        except OSError:
            address = bytes([3, len(target)]) + target.encode()
        sock.sendall(b'\x05\x01\x00' + address + struct.pack('!H', target_port))
        reply = reader.read(10)
        assert len(reply) == 10 and reply[1] == 0
        if tls:
            outgoing = ssl.MemoryBIO()
            client = ssl.create_default_context().wrap_bio(ssl.MemoryBIO(), outgoing, server_hostname=host)
            with contextlib.suppress(ssl.SSLWantReadError):
                client.do_handshake()
            sock.sendall(outgoing.read())
        else:
            sock.sendall(f'GET /synthetic HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
        return reader.readline(256).decode().strip()


def fake_address(port, host):
    labels = b''.join(bytes([len(part)]) + part.encode() for part in host.split('.')) + b'\0'
    query = struct.pack('!HHHHHH', 1234, 256, 1, 0, 0, 0) + labels + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        sock.sendto(query, ('127.0.0.1', port))
        reply = sock.recv(4096)
    assert struct.unpack('!H', reply[:2])[0] == 1234 and reply[3] & 15 == 0

    def skip_name(offset):
        while reply[offset]:
            if reply[offset] & 192 == 192:
                return offset + 2
            offset += 1 + reply[offset]
        return offset + 1

    offset = skip_name(12) + 4
    for _ in range(struct.unpack('!H', reply[6:8])[0]):
        offset = skip_name(offset)
        kind, _, _, length = struct.unpack('!HHIH', reply[offset:offset + 10])
        offset += 10
        if kind == 1 and length == 4:
            result = socket.inet_ntoa(reply[offset:offset + 4])
            assert result.startswith('198.18.'), 'Synthetic DNS did not issue a fake IP.'
            return result
        offset += length
    raise AssertionError('Synthetic DNS returned no IPv4 answer.')


def main():
    with contextlib.ExitStack() as resources:
        peers = []
        for cls, bind, marker in [(socketserver.ThreadingTCPServer, ('127.0.0.1', 0), b'original-cdn'),
                                  (IPv6Peer, ('::1', 0), b'resolved-cdn'),
                                  (socketserver.ThreadingTCPServer, ('127.0.0.1', 0), None)]:
            # IPv4 and IPv6 peers must use the same destination port.
            if marker == b'resolved-cdn':
                bind = ('::1', peers[0].server_address[1])
            server = resources.enter_context(cls(bind, RoutePeer if marker is None else DirectPeer))
            server.daemon_threads = True
            server.marker = marker
            peers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            resources.callback(server.shutdown)
        profile = fixture()
        peers[2].residual = (profile['residual']['server'], profile['residual']['port'])
        resolvers = []
        for answer in ('127.0.0.1', '::1'):
            server = resources.enter_context(socketserver.ThreadingTCPServer(('127.0.0.1', 0), DNSPeer))
            server.daemon_threads = True
            server.answer, server.questions = answer, []
            resolvers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            resources.callback(server.shutdown)
        target_port = peers[0].server_address[1]
        downstream = single_app.routing(profile, bytes(32), peers[2].server_address[1])
        downstream['mixed-port'] = free_port()
        downstream['external-controller'] = f'127.0.0.1:{free_port()}'
        downstream['interface-name'] = 'lo0' if sys.platform == 'darwin' else 'lo'
        downstream['tun']['auto-detect-interface'] = False
        downstream['ipv6'] = True
        downstream['dns'].update({'enable': True, 'ipv6': True,
                                 'nameserver': [f'tcp://127.0.0.1:{resolvers[1].server_address[1]}']})
        direct_dns = f'tcp://127.0.0.1:{resolvers[0].server_address[1]}'
        # Override only the external DNS endpoint; use the real policy renderer and core.
        old_dns = getattr(domain_policy, 'DIRECT_DNS', None)
        domain_policy.DIRECT_DNS = direct_dns
        resources.callback(setattr, domain_policy, 'DIRECT_DNS', old_dns)
        folder = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        policy = domain_policy.Policy(downstream, folder / 'policy.enc', bytes(32))
        downstream.update(policy.render([domain_policy.rule('exact', 'cdn.example.invalid', 'DIRECT')],
                                        downstream['interface-name']))
        downstream['hosts'] = {'cdn.example.invalid': '::1'}
        handoff = free_port()
        downstream['listeners'] = [{'name': 'synthetic-handoff', 'type': 'shadowsocks',
                                    'listen': '127.0.0.1', 'port': handoff,
                                    'cipher': 'aes-128-gcm', 'password': '0' * 64, 'udp': False}]
        upstream = json.loads((ROOT / 'macos/tun/capture.json.in').read_text().replace('__HANDOFF_KEY__', '0' * 64))
        upstream.update({'mixed-port': free_port(), 'external-controller': f'127.0.0.1:{free_port()}',
                         'secret': 'synthetic-api-key', 'tun': {'enable': False},
                         'hosts': {'cdn.example.invalid': '::1'}})
        dns_port = free_port()
        upstream['dns'].update({'enable': True, 'listen': f'127.0.0.1:{dns_port}'})
        upstream['proxies'][0]['port'] = handoff
        for cfg in (downstream, upstream):
            for protocol in ('HTTP', 'TLS'):
                cfg['sniffer']['sniff'][protocol]['ports'] = [target_port]
        start_core(resources, downstream)
        start_core(resources, upstream)
        for layer, cfg in [('routing', downstream), ('capture-and-routing', upstream)]:
            for tls in (False, True):
                for target, host, expected in [('127.0.0.1', 'cdn.example.invalid', 'original-cdn'),
                                                ('192.0.2.99', 'ordinary.example.invalid', 'one 192.0.2.99'),
                                                ('192.0.2.99', 'chatgpt.com', 'two 192.0.2.99'),
                                                ('192.0.2.99', 'www.apple.com', 'two 192.0.2.99')]:
                    actual = probe(cfg['mixed-port'], target, target_port, host, tls)
                    assert actual == expected, (layer, 'TLS' if tls else 'HTTP', host, actual, expected)
        for host, marker in [('ordinary.example.invalid', 'one'), ('chatgpt.com', 'two')]:
            target = fake_address(dns_port, host)
            assert probe(upstream['mixed-port'], target, target_port, host, True) == f'{marker} {host}', host
        assert probe(downstream['mixed-port'], 'direct-test.cn', target_port, 'direct-test.cn', True) == 'original-cdn', 'DIRECT used the proxy DNS resolver'
        assert 'direct-test.cn' in resolvers[0].questions, 'DIRECT DNS received no query'
        assert 'direct-test.cn' not in resolvers[1].questions, 'DIRECT query leaked to the default resolver'
        assert probe(downstream['mixed-port'], 'default-test.invalid', target_port, 'default-test.invalid', True) == 'one default-test.invalid'
        assert 'default-test.invalid' in resolvers[1].questions, 'Default route lost its DNS resolver'
        assert 'default-test.invalid' not in resolvers[0].questions, 'Default route used DIRECT DNS'
        print('20 real-core checks passed: CDN IP preservation, HTTP/TLS policies, fake-IP handoff, and separate DIRECT/default DNS.')


if __name__ == '__main__':
    main()
