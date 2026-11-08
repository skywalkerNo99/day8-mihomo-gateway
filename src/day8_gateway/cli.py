"""Password-interactive native deployment commands."""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import sys
import yaml

from . import config, container_key, controller, runtime, vault

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
    run.add_argument('--detach', action='store_true')
    run.add_argument('--tun', action='store_true', help='Explicitly enable system TUN; requires elevated OS privileges')
    commands.add_parser('status')
    commands.add_parser('stop')
    verify = commands.add_parser('verify')
    verify.add_argument('--proxy', help='Loopback HTTP/SOCKS proxy, e.g. http://127.0.0.1:7890 for macOS host Clash')
    mac = commands.add_parser('macos', help='Use the existing native Clash/Mihomo core with a native adapter')
    mac.add_argument('action', choices=['plan', 'deploy', 'restore'])
    mac.add_argument('--profile', type=Path, required=True, help='Existing on-disk original Clash profile')
    mac.add_argument('--core-config', type=Path, required=True, help='Existing core config containing controller/secret')
    mac.add_argument('--adapter-port', type=int, default=17889)
    mac.add_argument('--strict', action='store_true', help='Route all public TCP traffic through residual; strongest domain coverage')
    mac.add_argument('--apply', action='store_true', help='Explicit authorization to hot-apply the in-memory configuration')
    args = parser.parse_args()
    args.root = ROOT
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s %(message)s')

    if (args.command == 'macos' and args.action == 'deploy' and args.apply and not args.strict
            or args.command == 'run' and args.tun):
        raise UserError('当前 Muon 适配器仅支持 TCP，尚不能部署要求 UDP 的整机分流。请用 run 在回环验证。')

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
    elif args.command in ('status', 'stop', 'verify'):
        result = runtime.control(ROOT, args.command, **({'proxy': args.proxy} if args.command == 'verify' else {}))
        print(json.dumps(result, indent=2))
        if not result.get('ok'):
            return 1
    elif args.command == 'run':
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
            except (OSError, ValueError):
                raise UserError('容器自动解锁失败。请核对 vault 和密钥卷，必要时重新运行 enroll-container。') from None
        else:
            payload = unlock(args)
        cfg = config.native(payload, args.port, args.adapter_port, args.controller_port, args.tun, args.bind)
        runtime.start(payload, cfg, args, args.detach)
    elif args.command == 'macos':
        base, url, secret = core_settings(args)
        if args.action == 'restore':
            # Reloading the untouched original is explicit in the restore command.
            controller.request(url, secret, '/configs?force=true', {'path': str(args.profile.expanduser().resolve())}, 'PUT')
            try:
                status = runtime.control(ROOT, 'status')
                if status.get('mode') == 'adapter':
                    runtime.control(ROOT, 'stop')
            except OSError:
                pass
            print('Original on-disk profile restored; native adapter stopped.')
            return 0
        payload = unlock(args)
        base.update(read_yaml(args.profile))
        cfg = config.macos(payload, base, ROOT / 'rules', args.adapter_port, args.strict)
        cfg['external-controller'] = url.removeprefix('http://')
        cfg['secret'] = secret
        if args.action == 'plan' or not args.apply:
            print(json.dumps({'mode': 'strict' if args.strict else 'day8-default', 'native_adapter': True,
                              'rules': len(cfg['rules']), 'inline_providers': len(cfg['rule-providers']), 'public_udp': 'rejected', 'host_routing_changed': False}, indent=2))
            return 0
        current = controller.request(url, secret, '/configs')
        if not current.get('tun', {}).get('enable'):
            raise ValueError('Host Mihomo TUN is not enabled. Enable it yourself for full macOS traffic coverage, then retry.')
        runtime.start(payload, None, args, detach=True)
        try:
            controller.apply(url, secret, cfg)
        except Exception:
            runtime.control(ROOT, 'stop')
            raise RuntimeError('Host apply failed; native adapter stopped. Reload your original profile if necessary.') from None
        print('Native two-hop policy applied in memory. Original profile file was not modified.')
        print('After a core/app restart, run deploy again and enter the vault password.')
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
