"""Exercise the production rule generator using an isolated core and loopback SOCKS peers."""
import argparse
import contextlib
import json
import select
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from day8_gateway import config, controller
from test_vault_config import fixture

OK = bytes.fromhex('05000001000000000000')
FAIL = bytes.fromhex('05040001000000000000')

CLAUDE_SUFFIXES = (
    'ant.dev', 'anthropic.com', 'anthropic.team', 'antspace.dev', 'chatbotclaude.com',
    'clau.de', 'claude.ai', 'claude.app', 'claude.com', 'claude.dev', 'claude.new',
    'claude.site', 'claudeacademy.com', 'claudemcpclient.com', 'claudemcpcontent.com',
    'claudepages.dev', 'claudestudio.com', 'claudeusercontent.com',
)
CLAUDE_HOSTS = ('servd-anthropic-website.b-cdn.net', 'o1158394.ingest.us.sentry.io')


def address(reader):
    version, command, _, kind = reader.read(4)
    assert (version, command) == (5, 1)
    if kind == 3:
        host = reader.read(reader.read(1)[0]).decode('ascii')
    else:
        family, size = {1: (socket.AF_INET, 4), 4: (socket.AF_INET6, 16)}[kind]
        host = socket.inet_ntop(family, reader.read(size))
    return host, struct.unpack('!H', reader.read(2))[0]


def handshake(reader, sock, method):
    assert reader.read(1) == b'\x05'
    methods = reader.read(reader.read(1)[0])
    assert method in methods
    sock.sendall(bytes((5, method)))


class FirstHop(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(4)
            handshake(self.rfile, self.request, 0)
            dest = address(self.rfile)
            if self.server.first_down.is_set():
                self.request.sendall(FAIL)
                return
            two_hops = dest == self.server.residual
            if two_hops and self.server.second_down.is_set():
                self.request.sendall(FAIL)
                return
            self.request.sendall(OK)
            if two_hops:
                handshake(self.rfile, self.request, 2)
                assert self.rfile.read(1) == b'\x01'
                user = self.rfile.read(self.rfile.read(1)[0])
                password = self.rfile.read(self.rfile.read(1)[0])
                assert (user, password) == (b'test-user', b'test-pass')
                self.request.sendall(b'\x01\x00')
                dest = address(self.rfile)
                self.request.sendall(OK)
            if self.rfile.read(1) == b'?':
                marker = 'two' if two_hops else 'one'
                self.request.sendall(f'{marker} {dest[0]}\n'.encode())
        except (OSError, EOFError, IndexError, ValueError):
            return


def probe(port, host):
    with socket.create_connection(('127.0.0.1', port), 4) as sock, sock.makefile('rb') as reader:
        sock.settimeout(4)
        sock.sendall(b'\x05\x01\x00')
        assert reader.read(2) == b'\x05\x00'
        data = host.encode('ascii')
        sock.sendall(bytes([5, 1, 0, 3, len(data)]) + data + b'\x01\xbb')
        reply = reader.read(10)
        if len(reply) != 10 or reply[1] != 0:
            return ''
        sock.sendall(b'?')
        return reader.readline(256).decode().strip()


def check_udp_rejection(port):
    # Both domain names resolve to this test sink if a DIRECT fallback is taken.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        sink.bind(('127.0.0.1', 0))
        with socket.create_connection(('127.0.0.1', port), 4) as ctl, ctl.makefile('rb') as reader:
            ctl.sendall(bytes([5, 1, 0]))
            assert reader.read(2) == bytes([5, 0])
            ctl.sendall(bytes.fromhex('05030001000000000000'))
            assert reader.read(4) == bytes([5, 0, 0, 1])
            relay_host = socket.inet_ntoa(reader.read(4))
            relay_port = struct.unpack('!H', reader.read(2))[0]
            assert relay_host in ('127.0.0.1', '0.0.0.0') and relay_port
            for host in (b'udp-test.apple.com', b'udp-route.invalid', b'udp-test.claude.ai'):
                packet = bytes([0, 0, 0, 3, len(host)]) + host + struct.pack('!H', sink.getsockname()[1]) + b'test'
                client.sendto(packet, ('127.0.0.1', relay_port))
            ready, _, _ = select.select([sink, client], [], [], .75)
            assert not ready, 'A blocked UDP datagram reached the sink or returned data.'


def run(binary):
    profile = fixture()
    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), FirstHop) as peer, tempfile.TemporaryDirectory() as tmp:
        peer.daemon_threads = True
        peer.first_down = threading.Event()
        peer.second_down = threading.Event()
        peer.residual = (profile['residual']['server'], profile['residual']['port'])
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        with contextlib.ExitStack() as ports:
            socks, api = [ports.enter_context(socket.socket()) for _ in range(2)]
            for sock in (socks, api):
                sock.bind(('127.0.0.1', 0))
            port, api_port = socks.getsockname()[1], api.getsockname()[1]
        cfg = config.native(profile, port, peer.server_address[1], api_port)
        # Require that the only remotely dialled endpoint in this test is the local peer.
        cfg['log-level'] = 'info'
        cfg['dns']['enable'] = False
        cfg['hosts'] = dict.fromkeys(('udp-test.apple.com', 'udp-route.invalid', 'udp-test.claude.ai'), '127.0.0.1')
        cfg['sniffer']['enable'] = False
        with subprocess.Popen([str(binary), '-d', tmp, '-f', '/dev/stdin'], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as core:
            try:
                core.stdin.write(json.dumps(cfg).encode())
                core.stdin.close()
                for _ in range(100):
                    try:
                        providers = controller.request(f'http://127.0.0.1:{api_port}', cfg['secret'], '/providers/rules')
                        loaded = providers.get('providers') or {}
                        if all((loaded.get(name) or {}).get('ruleCount', 0) > 0
                               for name in cfg['rule-providers']) and probe(port, 'apple.com') == 'two apple.com':
                            break
                        time.sleep(.05)
                    except OSError:
                        if core.poll() is not None:
                            raise RuntimeError('Core exited before becoming ready.')
                        time.sleep(.05)
                else:
                    raise RuntimeError('Core rules did not become ready.')
                routes = [('apple.com','two'), ('a.icloud.com','two'), ('chatgpt.com','two'),
                          ('api.openai.com','two'), ('www.baidu.com','one'),
                          ('new.example.invalid','one'), ('js.stripe.com','one'),
                          ('challenges.cloudflare.com','one')]
                routes += [(host, 'two') for domain in CLAUDE_SUFFIXES
                           for host in (domain, 'nested.test.' + domain)]
                routes += [(host, 'two') for host in (*CLAUDE_HOSTS, 'api.anthropic.com',
                           'platform.claude.com', 'sso.claude.com', 'downloads.claude.ai',
                           'bridge.claudeusercontent.com', 'artifact.frame.claudeusercontent.com')]
                routes += [(host, 'one') for host in ('notclaude.ai', 'claude.ai.example.invalid',
                           'anthropic.com.example.invalid', 'storage.googleapis.com', 'github.com',
                           'registry.npmjs.org', 'http-intake.logs.us5.datadoghq.com', 'cdn.usefathom.com',
                           'other.ingest.us.sentry.io', 'b-cdn.net', 'other.b-cdn.net')]
                for host, expected in routes:
                    actual = probe(port, host)
                    assert actual == f'{expected} {host}', (host, actual)
                check_udp_rejection(port)
                for down in (peer.second_down, peer.first_down):
                    down.set()
                    for host in ('chatgpt.com', 'claude.ai', 'api.anthropic.com', 'new.example.invalid'):
                        try:
                            result = probe(port, host)
                        except (ConnectionResetError, BrokenPipeError):
                            result = ''
                        expected = 'one ' + host if down is peer.second_down and host.endswith('.invalid') else ''
                        assert result == expected, (host, result)
                    down.clear()
                    assert probe(port, 'chatgpt.com') == 'two chatgpt.com'
                    assert probe(port, 'claude.ai') == 'two claude.ai'
                print(f'{len(routes)} TCP routes, 3 UDP rejections, both hop failures and both recoveries passed (synthetic loopback).')
            except Exception:
                core.terminate()
                print(core.stdout.read().decode(errors='replace'), file=sys.stderr)
                raise
            finally:
                core.terminate()
                peer.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mihomo', type=Path, default=ROOT / '.tools/mihomo')
    run(parser.parse_args().mihomo.resolve())
