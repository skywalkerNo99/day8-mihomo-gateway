"""One bounded two-hop TCP trace and a separate residential UDP control negotiation."""
import argparse
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from day8_gateway import adapter, cli, config, runtime, verify


async def associate(payload):
    res = payload['residual']
    stream = await adapter.Muon(payload['day8']).connect(res['server'], res['port'])
    buf = bytearray()

    async def read(n):
        while len(buf) < n:
            buf.extend(await stream.receive())
        out = bytes(buf[:n])
        del buf[:n]
        return out

    try:
        await stream.send(bytes([5, 1, 2]))
        if await read(2) != bytes([5, 2]):
            return {'stage': 'socks_auth_method'}
        user, pw = (res[key].encode() for key in ('username', 'password'))
        await stream.send(bytes([1, len(user)]) + user + bytes([len(pw)]) + pw)
        if await read(2) != bytes([1, 0]):
            return {'stage': 'socks_authenticate'}
        await stream.send(bytes.fromhex('05030001000000000000'))
        version, reply = await read(2)
        if version != 5:
            return {'stage': 'invalid_socks_version'}
        return {'stage': 'udp_associate_reply', 'reply_code': reply}
    finally:
        await stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, default=ROOT / 'secrets/deployment.enc.json')
    args = parser.parse_args()
    payload = cli.unlock(args)
    results = {'udp_payload_verified': False}
    try:
        results['residential_control_via_day8'] = asyncio.run(asyncio.wait_for(associate(payload), 18))
    except Exception:
        results['residential_control_via_day8'] = {'stage': 'negotiation_incomplete'}
    with tempfile.TemporaryDirectory(prefix='day8-check-', dir='/tmp') as tmp:
        with ExitStack() as stack:
            ports = []
            for _ in range(3):
                sock = stack.enter_context(socket.socket())
                sock.bind(('127.0.0.1', 0))
                ports.append(sock.getsockname()[1])
        args.root = Path(tmp)
        args.mihomo = ROOT / '.tools/mihomo'
        args.port, args.adapter_port, args.controller_port = ports
        cfg = config.native(payload, *ports)
        runtime.start(payload, cfg, args, detach=True)
        try:
            results['chatgpt_https'] = verify.check(f'http://127.0.0.1:{args.port}', payload['expected_exit_ip'], 1)
        finally:
            runtime.control(args.root, 'stop')
            for _ in range(100):
                if not (args.root / '.local/control.sock').exists():
                    break
                time.sleep(.05)
    print(json.dumps(results, indent=2))
    return 0 if results['chatgpt_https']['ok'] and results['residential_control_via_day8'].get('reply_code') == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt):
        sys.exit('Live check incomplete. Check password, local core and network.')
