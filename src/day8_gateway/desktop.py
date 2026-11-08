"""Local bridge for the macOS app. Never return decrypted profiles to the GUI."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from . import catalog, macos_keychain, node_updates, official, runtime

ROOT = Path.home() / 'Library/Application Support/Day8Gateway'
BASE = ROOT / 'deployment.enc.json'


def docker(context, *args, packet=None):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', context):
        raise ValueError('Invalid Docker context.')
    binary = shutil.which('docker')
    if not binary:
        raise ValueError('Docker CLI unavailable.')
    return subprocess.run([binary, '--context', context, *args], input=packet, capture_output=True,
                          text=True, timeout=150, check=True).stdout.strip()


def send_packet(context, target, packet):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', target):
        raise ValueError('Invalid container.')
    container = docker(context, 'inspect', '--format',
                       '{{.Id}} {{index .Config.Labels "com.docker.compose.service"}} {{.Platform}}', target).split()
    if len(container) != 3 or container[1:] != ['day8-gateway','linux'] or not re.fullmatch('[a-f0-9]{64}',container[0]):
        raise ValueError('Select a Linux gateway container.')
    docker(context, 'exec', '-i', container[0], 'python3', 'gateway', 'accept-node', packet=packet)


def main(args):
    action, *params = args
    if action == 'domain-rules':
        return runtime.control(ROOT, action)
    if action == 'domain-events':
        return runtime.control(ROOT, action, query=params[0] if params else '')
    if action == 'change-domain-rule':
        operation, revision, index, *fields = params
        item = dict(zip(('kind', 'value', 'target'), fields, strict=True)) if operation == 'save' else None
        return runtime.control(ROOT, action, action=operation, revision=revision, index=int(index), rule=item)
    if action in ('probe-status', 'route-delays'):
        return runtime.control(ROOT, action)
    if action == 'probe-interval':
        return runtime.control(ROOT, action, minutes=int(params[0]))
    if action in ('routes', 'select-route', 'import-routes'):
        key = macos_keychain.read_key(BASE)
        if action == 'import-routes':
            catalog.save_export(Path(params[0]), BASE, ROOT / 'routes.local.enc.json', key, macos_keychain.unlock(BASE))
        entries = catalog.read(ROOT, key)
        if action == 'select-route':
            entry = next((e for e in entries if e['id'] == params[0]), None)
            if not entry or not entry['node'] or entry['maintenance']:
                return {'ok': False, 'error': 'UnavailableRoute'}
            packet = node_updates.seal(BASE, key, macos_keychain.unlock(BASE), entry['node'])
            result = runtime.control(ROOT, 'apply-node', packet=packet)
            if not result.get('ok'):
                return result
        active = runtime.control(ROOT, 'status')['route_token']
        return {'ok': True, **catalog.summary(entries, key, active)}
    if action == 'health':
        result = runtime.control(ROOT, 'health')
        if (ROOT / 'single-app.enabled').is_file():
            from .tun_verify import interface
            result['systemTunnel'] = interface('1.1.1.1')
        return result
    if action == 'verify':
        command = 'verify-system-tun' if (ROOT / 'single-app.enabled').is_file() else 'verify'
        return {'ok': runtime.control(ROOT, command)['ok']}
    if action == 'contexts':
        rows = docker('default', 'context', 'ls', '--format', '{{.Name}}').splitlines()
        current = subprocess.run([shutil.which('docker'), 'context', 'show'], text=True, capture_output=True,
                                 timeout=10, check=True).stdout.strip()
        return {'ok': True, 'contexts': rows, 'current': current}
    if action == 'targets':
        rows = docker(params[0], 'ps', '--no-trunc', '--filter', 'label=com.docker.compose.service=day8-gateway',
                      '--format', '{{.Names}} {{.ID}}').splitlines()
        identities = dict(row.split() for row in rows)
        return {'ok': True, 'targets': sorted(identities), 'identities': identities}
    if action == 'sync-official':
        node = official.desktop_node()
        if node is None:
            return {'ok': False, 'error': 'NoExportedNode'}
        packet = node_updates.seal(BASE, macos_keychain.read_key(BASE), macos_keychain.unlock(BASE), node)
        return runtime.control(ROOT, 'apply-node', packet=packet)
    key = macos_keychain.read_key(BASE)
    base = macos_keychain.unlock(BASE)
    if action == 'import':
        source = Path(params[0])
        with source.open('rb') as stream:
            data = stream.read(node_updates.LIMIT + 1)
        if len(data) > node_updates.LIMIT:
            raise ValueError('Input too large.')
        if source.suffix == '.enc':
            packet = data.decode('ascii').strip()
            node_updates.unseal(BASE, key, base, packet)
        else:
            doc = json.loads(data)
            packet = node_updates.seal(BASE, key, base, doc.get('day8', doc))
        return runtime.control(ROOT, 'apply-node', packet=packet)
    if action == 'push':
        context, target = params
        node_updates.load_saved(ROOT, BASE, key, base)
        packet = node_updates.seal(BASE, key, base, base['day8'])
        send_packet(context, target, packet)
        return {'ok': True}
    raise ValueError('Unknown action.')


if __name__ == '__main__':
    try:
        result = main(sys.argv[1:])
        print(json.dumps(result))
        sys.exit(0 if result.get('ok') else 1)
    except Exception as exc:
        # Subprocess errors, filenames and parser messages may include private values.
        print(json.dumps({'ok': False, 'error': type(exc).__name__}))
        sys.exit(1)
