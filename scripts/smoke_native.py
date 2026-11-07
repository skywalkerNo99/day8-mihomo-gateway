#!/usr/bin/env python3
"""Synthetic native Mihomo startup, API rule parsing and supervised restart.

No real credentials, external target traffic, TUN or system proxy changes.
"""
from argparse import Namespace
from pathlib import Path
import os
import signal
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from day8_gateway import config, controller, runtime
from test_vault_config import fixture


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(prefix='day8-smoke-') as tmp:
        args = Namespace(root=Path(tmp), port=port(), adapter_port=port(), controller_port=port(),
                         mihomo=ROOT / '.tools/mihomo')
        cfg = config.native(fixture(), args.port, args.adapter_port, args.controller_port)
        runtime.start(fixture(), cfg, args, detach=True)
        try:
            first = runtime.control(args.root, 'status')
            url = f'http://127.0.0.1:{args.controller_port}'
            merged = config.macos(fixture(), cfg, ROOT / 'rules', args.adapter_port)
            controller.apply(url, cfg['secret'], merged)
            effective = controller.request(url, cfg['secret'], '/rules')['rules']
            assert any(r.get('proxy') == config.EGRESS for r in effective)
            print('Native core accepted inline domain/IP rules and AND UDP rejects.')
            strict = config.macos(fixture(), cfg, ROOT / 'rules', args.adapter_port, strict=True)
            controller.apply(url, cfg['secret'], strict)
            print('Native core accepted strict policy via in-memory payload API.')
            os.kill(first['mihomo_pid'], signal.SIGTERM)
            for _ in range(150):
                time.sleep(.1)
                current = runtime.control(args.root, 'status')
                if current['mihomo_pid'] != first['mihomo_pid']:
                    # Recheck API readiness after the process has been created.
                    try:
                        controller.request(url, cfg['secret'], '/version')
                        break
                    except OSError:
                        continue
            else:
                raise AssertionError('Native core did not recover.')
            print('Native Mihomo child restart passed.')
        finally:
            runtime.control(args.root, 'stop')
            for _ in range(100):
                if not (args.root / '.local/control.sock').exists():
                    break
                time.sleep(.05)
    return 0


if __name__ == '__main__':
    sys.exit(main())
