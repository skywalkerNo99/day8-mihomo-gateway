"""Password-interactive native deployment commands."""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import sys
import subprocess
import time
import yaml

from . import config, container_key, linux_tun, node_updates, runtime, vault

ROOT = Path(__file__).resolve().parents[2]


class UserError(Exception):
    """An explicitly safe message containing no values from config/parser errors."""


def password(prompt='Vault password: '):
    if not sys.stdin.isatty():
        raise UserError('请在本机交互终端运行，以便隐藏输入密码。')
    return getpass.getpass(prompt)


def unlock(args):
    return config.validate(vault.load(args.vault, password()))


def read_yaml(path):
    result = yaml.safe_load(Path(path).expanduser().read_text())
    if not isinstance(result, dict):
        raise ValueError('Expected a YAML mapping.')
    return result


def core_settings(args):
    base = read_yaml(args.core_config)
    endpoint = base.get('external-controller', '127.0.0.1:9090')
    if endpoint.startswith(('0.0.0.0:', ':')):
        endpoint = '127.0.0.1:' + endpoint.rsplit(':', 1)[1]
    return base, 'http://' + endpoint, str(base.get('secret', ''))


def native_args(parser):
    parser.add_argument('--port', type=int, default=17890)
    parser.add_argument('--adapter-port', type=int, default=17889)
    parser.add_argument('--controller-port', type=int, default=17891)
    parser.add_argument('--mihomo', type=Path, default=ROOT / '.tools/mihomo')
    parser.add_argument('--bind', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1', help='Explicit listener address; use 0.0.0.0 only behind container/firewall access controls')


def main():
    parser = argparse.ArgumentParser(description='Native Day8 → residual → destination gateway')
    parser.add_argument('--vault', type=Path, default=ROOT / 'secrets/deployment.enc.json')
    commands = parser.add_subparsers(dest='command', required=True)
    seal = commands.add_parser('seal', help='Encrypt minimal deployment credentials, hidden terminal input')
    source = seal.add_mutually_exclusive_group(required=True)
    source.add_argument('--source', type=Path, help='Import a legacy muon.json + mihomo.json directory')
    source.add_argument('--input', type=Path, help='Read an existing deployment JSON without copying it')
    commands.add_parser('rekey', help='Change vault password')
    run = commands.add_parser('run', help='Native standalone gateway; changes no system routing by default')
    commands.add_parser('enroll-container', help='Unlock once and enroll this vault in a private Docker volume')
    native_args(run)
    run.add_argument('--auto-unlock', action='store_true', help='Use the enrolled key in the mounted Linux container volume')
    run.add_argument('--connection-logs', action='store_true', help='Print filtered Mihomo connection events to stdout')
    run.add_argument('--detach', action='store_true')
    run.add_argument('--tun', action='store_true', help='Explicitly enable system TUN; requires elevated OS privileges')
    container_tun = commands.add_parser('run-container-tun', help='Run the complete gateway in a dedicated Linux container network namespace')
    native_args(container_tun)
    container_tun.set_defaults(port=7890, tun=True, auto_unlock=True, connection_logs=True, detach=False)
    mon = commands.add_parser('monitor', help='Probe both outbounds every 30 seconds')
    mon.add_argument('--once', action='store_true')
    mon.add_argument('--json', action='store_true', help='Print one JSON object per sample')
    commands.add_parser('accept-node', help='Apply an authenticated node update from stdin')
    commands.add_parser('status')
    commands.add_parser('stop')
    verify = commands.add_parser('verify')
    verify.add_argument('--proxy', help='Explicit loopback HTTP/SOCKS proxy; macOS routing core defaults to port 17890')
    mac = commands.add_parser('macos', help='Install or manage the macOS single-app TUN')
    mac.add_argument('action', choices=['install', 'migrate', 'preflight', 'check', 'rollback', 'upgrade', 'remove'])
    args = parser.parse_args()
    args.root = ROOT
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')

    if args.command == 'macos':
        script = 'macos_persistent.py' if args.action in ('install', 'upgrade', 'remove') else 'macos_takeover.py'
        return subprocess.run([sys.executable, 'scripts/' + script, args.action], cwd=ROOT).returncode
    if args.command == 'run' and args.tun:
        raise UserError('整机 TUN 请使用 gateway macos install；Linux 请按原生部署文档操作。')

    if args.command == 'seal':
        if args.vault.exists():
            raise UserError('加密文件已存在；修改密码请使用 rekey，或用 --vault 指定新的保存位置。')
        payload = config.import_legacy(args.source) if args.source else config.validate(json.loads(args.input.read_text()))
        first = password('Choose vault password: ')
        if not first:
            raise UserError('密码不能为空，尚未保存任何文件。请重新运行。')
        if first != password('Confirm vault password: '):
            raise UserError('两次密码不一致，尚未保存任何文件。请重新运行。')
        vault.save(args.vault, payload, first)
        # Confirm the exact file round-trips before reporting success.
        if vault.load(args.vault, first) != payload:
            raise RuntimeError('Encrypted vault verification failed.')
        print('Encrypted profile saved and verified. Password and plaintext were not saved.')
    elif args.command == 'enroll-container':
        container_key.enroll(args.vault, password())
        print('容器自动解锁已启用。')
    elif args.command == 'rekey':
        payload = unlock(args)
        first = password('New vault password: ')
        if not first or first != password('Confirm new vault password: '):
            raise UserError('两次密码需一致且不能为空，原加密文件未变更。')
        vault.save(args.vault, payload, first)
        print('Vault password changed.')
    elif args.command in ('monitor', 'accept-node', 'status', 'stop', 'verify'):
        root = ROOT
        managed = Path.home() / 'Library/Application Support/Day8Gateway'
        if sys.platform == 'darwin' and (managed / 'installation.json').exists() and not (ROOT / '.local/control.sock').exists():
            root = managed
            if args.command == 'stop':
                raise UserError('登录服务由 launchd 管理。回退请使用 gateway macos rollback，卸载用 gateway macos remove。')
        if args.command == 'monitor':
            while True:
                try:
                    result = runtime.control(root, 'health')
                except (OSError, RuntimeError):
                    result = {'ok': False}
                if args.json:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                else:
                    nodes = result.get('nodes', {})
                    def latency(name):
                        probe = nodes.get(name, {})
                        return str(probe['ms']) + ' ms' if probe.get('online') else 'unavailable'
                    print(time.strftime('%H:%M:%S'), ' Day8:', latency(config.TRANSPORT),
                          ' Day8 → 1024:', latency(config.EGRESS), ' active:', result.get('connections', '—'), flush=True)
                if args.once:
                    return 0 if result.get('ok') and all(n['online'] for n in result['nodes'].values()) else 1
                time.sleep(30)
        if args.command == 'accept-node':
            packet = sys.stdin.read(node_updates.LIMIT + 1).strip()
            result = runtime.control(root, 'apply-node', packet=packet)
            print('Applied and verified.' if result.get('ok') else 'Update failed; check gateway status.')
            return 0 if result.get('ok') else 1
        result = runtime.control(root, args.command, **({'proxy': args.proxy} if args.command == 'verify' else {}))
        print(json.dumps(result, indent=2))
        if not result.get('ok'):
            return 1
    elif args.command in ('run', 'run-container-tun'):
        if args.tun and os.geteuid() != 0:
            raise ValueError('TUN requires explicit root/CAP_NET_ADMIN setup. Start without --tun for local proxy mode.')
        if not args.mihomo.is_file():
            raise ValueError('Native Mihomo is missing; run python3 gateway bootstrap.')
        ports = [args.port, args.adapter_port, args.controller_port]
        if len(set(ports)) != 3 or any(not 1 <= p <= 65535 for p in ports):
            raise ValueError('Use three different valid TCP ports.')
        if args.auto_unlock:
            try:
                payload = container_key.unlock(args.vault)
                args.update_key = container_key.read_key()
                node_updates.load_saved(ROOT, args.vault, args.update_key, payload)
            except (OSError, ValueError):
                raise UserError('容器自动解锁失败。请核对 vault 和密钥卷，必要时重新运行 enroll-container。') from None
        else:
            payload = unlock(args)
        cfg = config.native(payload, args.port, args.adapter_port, args.controller_port, args.tun, args.bind)
        if args.command == 'run-container-tun':
            linux_tun.configure(cfg)
            linux_tun.prepare()
        runtime.start(payload, cfg, args, args.detach)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except vault.VaultError:
        print('密码错误，或加密文件已损坏/不受支持。没有输出解密内容。', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration, yaml.YAMLError):
        # Config/parser exceptions may contain credentials; never echo their raw text.
        print('Operation failed. Check prerequisites, ports, input format and password; see docs/troubleshooting.md.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
