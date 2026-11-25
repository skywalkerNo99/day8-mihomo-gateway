#!/usr/bin/env python3
"""Verify literal-IP routing with an isolated real core and synthetic loopback peers."""
import contextlib
import json
import argparse
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from day8_gateway import config, controller, domain_policy, linux_tun
from test_vault_config import fixture
from smoke_routes import FirstHop


class DirectPeer(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(4)
        if self.rfile.read(1) == b'?':
            self.request.sendall(b'direct\n')


def probe(port, host, target_port):
    with socket.create_connection(('127.0.0.1', port), 4) as sock, sock.makefile('rb') as reader:
        sock.settimeout(4)
        sock.sendall(b'\x05\x01\x00')
        assert reader.read(2) == b'\x05\x00'
        try:
            address = bytes([1]) + socket.inet_aton(host)
        except OSError:
            encoded = host.encode('ascii')
            address = bytes([3, len(encoded)]) + encoded
        sock.sendall(bytes([5, 1, 0]) + address + struct.pack('!H', target_port))
        reply = reader.read(10)
        assert len(reply) == 10 and reply[1] == 0
        sock.sendall(b'?')
        return reader.readline(256).decode().strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--linux-container', action='store_true')
    args = parser.parse_args()
    with contextlib.ExitStack() as resources:
        root = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix='day8-cn-smoke-')))
        first = resources.enter_context(socketserver.ThreadingTCPServer(('127.0.0.1', 0), FirstHop))
        direct = resources.enter_context(socketserver.ThreadingTCPServer(('127.0.0.1', 0), DirectPeer))
        first.daemon_threads = direct.daemon_threads = True
        first.first_down = threading.Event()
        first.second_down = threading.Event()
        profile = fixture()
        first.residual = (profile['residual']['server'], profile['residual']['port'])
        for server in (first, direct):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            resources.callback(server.shutdown)
        with contextlib.ExitStack() as ports:
            socks, api = [ports.enter_context(socket.socket()) for _ in range(2)]
            for sock in (socks, api):
                sock.bind(('127.0.0.1', 0))
            port, api_port = socks.getsockname()[1], api.getsockname()[1]
        cfg = config.native(profile, port, first.server_address[1], api_port)
        loopback = 'lo0' if sys.platform == 'darwin' else 'lo'
        cfg['interface-name'] = loopback
        cfg['tun']['auto-detect-interface'] = False
        cfg['sniffer']['enable'] = False
        if args.linux_container:
            linux_tun.configure(cfg)
            cfg['tun']['enable'] = False
            cfg['dns'].pop('listen')
        cfg['rule-providers']['cn-networks'] = {'type': 'inline', 'behavior': 'ipcidr',
                                                'payload': ['127.0.0.1/32']}
        if not args.linux_container:
            policy = domain_policy.Policy(cfg, root / 'policy.enc', bytes(32))
            cfg.update(policy.render([], loopback))
        cfg['hosts'] = {'direct-test.cn': '127.0.0.1'}
        process = subprocess.Popen([str(ROOT / '.tools/mihomo'), '-d', str(root), '-f', '/dev/stdin'],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        resources.callback(process.wait, timeout=10)
        resources.callback(process.terminate)
        process.stdin.write(json.dumps(cfg).encode())
        process.stdin.close()
        for _ in range(100):
            try:
                providers = controller.request(f'http://127.0.0.1:{api_port}', cfg['secret'], '/providers/rules')['providers']
                if all((providers.get(name) or {}).get('ruleCount', 0) for name in cfg['rule-providers']):
                    with socket.create_connection(('127.0.0.1', port), 1):
                        break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError('Core exited before becoming ready.') from None
            time.sleep(.05)
        else:
            raise RuntimeError('Core rules did not become ready.')
        assert probe(port, '127.0.0.1', direct.server_address[1]) == 'direct', 'CN literal IP did not use DIRECT'
        assert probe(port, 'direct-test.cn', direct.server_address[1]) == 'direct', '.cn domain did not use DIRECT'
        assert probe(port, '192.0.2.99', 443) == 'one 192.0.2.99', 'Non-CN test IP did not use Day8'
        assert probe(port, 'chatgpt.com', 443) == 'two chatgpt.com', 'Residential policy was not retained'
        print('CN domain/IP DIRECT, ordinary IP Day8, and protected domain two-hop routing passed.')


if __name__ == '__main__':
    main()
