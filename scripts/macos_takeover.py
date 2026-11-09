"""Install the single-app TUN or recover the network used before migration."""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
from types import SimpleNamespace
import socket
import subprocess
import sys
import tempfile
import time

import macos_persistent as install
from day8_gateway import catalog, controller, macos_keychain, node_updates, residual, runtime, single_app, verify
from day8_gateway.tun_verify import interface
from day8_gateway.cli import core_settings

ROOT, HOME = install.ROOT, install.SUPPORT
MARK = HOME / 'single-app.enabled'
STATE = HOME / 'tun-migration.json'
ASSETS = ROOT / 'macos/tun'
SESSION = ROOT / '.tools/day8-clash-session'
ADMIN = ASSETS / 'admin.sh'


def admin(action, *args, key=None, output=False):
    return subprocess.run(['/usr/bin/sudo', '-n', '/bin/sh', str(ADMIN), action, *map(str, args)],
                          input=key + '\n' if key is not None else None, text=True,
                          stdout=subprocess.PIPE if output else None, check=True)


def session(action, check=True):
    status = subprocess.run([str(SESSION), action], check=check)
    if not check and status.returncode not in (0, 1):
        raise RuntimeError('Unable to inspect ClashX session.')
    return status.returncode == 0


def wait_for(test, message, seconds=25):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if test():
                return
        except (OSError, RuntimeError, KeyError, subprocess.SubprocessError):
            pass
        time.sleep(.35)
    raise RuntimeError(message)


def agent(mode='standalone'):
    install.ctl('kickstart', '-k', install.TARGET)
    wait_for(lambda: runtime.control(HOME, 'status')['mode'] == mode, 'Login agent startup failed.')


def authorize():
    # This runs only in the user's terminal. Password input is never captured.
    if not sys.stdin.isatty():
        probe = subprocess.run(['/usr/bin/sudo', '-n', 'true'], capture_output=True)
        if probe.returncode:
            raise RuntimeError('Run this operation in a local terminal for administrator authorization.')
    subprocess.run(['/usr/bin/sudo', '-v'], check=True)


def check_ports(include_adapter=None):
    if 'utun63' in subprocess.check_output(['/sbin/ifconfig', '-l'], text=True).split():
        raise RuntimeError('utun63 already in use.')
    with ExitStack() as stack:
        ports = [single_app.PORT, single_app.CONTROLLER, single_app.HANDOFF]
        if include_adapter is not None:
            ports.append(include_adapter)
        for port in ports:
            probe = stack.enter_context(socket.socket())
            probe.bind(('127.0.0.1', port))


def previous_session():
    state = {'hadRunning': session('running', check=False), 'hadLogin': session('login', check=False)}
    if not state['hadRunning']:
        # The capture port must also be free when no prior Clash core owns it.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 7890))
        return state
    saved = json.loads(install.STATE.read_text()) if install.STATE.is_file() else {}
    core = Path(saved.get('core', Path.home() / '.config/clash.meta/config.yaml'))
    prefs = plistlib.loads(subprocess.check_output(['/usr/bin/defaults', 'export', install.svc.APP_ID, '-']))
    source = saved.get('source')
    profile = Path(source) if source else core.parent / (prefs['selectConfigName'] + '.yaml')
    if not profile.is_file():
        raise RuntimeError('Previous Clash profile missing; cannot provide safe recovery.')
    _, url, secret = core_settings(SimpleNamespace(core_config=core))
    active = controller.request(url, secret, '/configs')
    if not active.get('tun', {}).get('enable') or active.get('mode') != 'rule':
        raise RuntimeError('Previous Clash must have TUN and rule mode enabled for migration.')
    state.update({'profile': str(profile), 'core': str(core)})
    return state


def unlocked():
    vault = HOME / 'deployment.enc.json'
    payload = macos_keychain.unlock(vault)
    key = macos_keychain.read_key(vault)
    residual.load(HOME, vault, key, payload)
    node_updates.load_saved(HOME, vault, key, payload)
    return payload, key


def install_binary(package):
    install.atomic(HOME / 'mihomo', gzip.decompress(package.read_bytes()))
    (HOME / 'mihomo').chmod(0o700)


def compile_session():
    SESSION.parent.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wno-deprecated-declarations', '-framework', 'AppKit',
                    '-framework', 'CoreServices', str(ASSETS / 'clash-session.m'), '-o', str(SESSION)], check=True)


def archive():
    arch = {'arm64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    entry = json.loads((ROOT / 'scripts/mihomo-lock.json').read_text())['platforms']['darwin-' + arch]
    path = ROOT / '.tools/single-tun.gz'
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        with tempfile.NamedTemporaryFile(dir=path.parent) as tmp:
            subprocess.run(['curl', '-q', '-fLsS', '--max-time', '120', '-o', tmp.name, entry['url']], check=True)
            data = Path(tmp.name).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise RuntimeError('Mihomo archive failed verification.')
            install.atomic(path, data)
    return path


def loopback_trial(payload, key, binary):
    # Same static capture policy and authenticated handoff, with auto-routing disabled.
    cfg = json.loads((ASSETS / 'capture.json.in').read_text().replace('__HANDOFF_KEY__', key))
    cfg['tun']['enable'] = False
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        cfg['mixed-port'] = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as folder:
        proc = subprocess.Popen([str(binary), '-d', folder, '-f', '/dev/stdin'],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.stdin.write(json.dumps(cfg).encode())
            proc.stdin.close()
            def ready():
                if proc.poll() is not None:
                    raise ValueError('Loopback capture core failed.')
                with socket.create_connection(('127.0.0.1', cfg['mixed-port']), timeout=.3):
                    return True
            wait_for(ready, 'Loopback listener failed.')
            proxy = 'http://127.0.0.1:' + str(cfg['mixed-port'])
            if not verify.check(proxy, payload['expected_exit_ip'], 1)['ok']:
                raise RuntimeError('Authenticated local handoff or residential exit verification failed.')
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def preflight():
    payload, key = unlocked()
    status = runtime.control(HOME, 'status')
    binary = ROOT / '.tools/mihomo'
    if status['mode'] == 'standalone':
        loopback_trial(payload, single_app.link_key(key), binary)
        print('Current single-app handoff and fixed ChatGPT exit passed.', flush=True)
        return
    # One-time check for legacy installed agents; no compatibility deployment is created.
    cfg = single_app.routing(payload, key, status['adapter_port'])
    with tempfile.TemporaryDirectory() as folder:
        proc = subprocess.Popen([str(binary), '-d', folder, '-f', '/dev/stdin'], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc.stdin.write(json.dumps(cfg).encode())
            proc.stdin.close()
            def ready():
                if proc.poll() is not None:
                    raise ValueError('Trial routing core failed.')
                with socket.create_connection(('127.0.0.1', single_app.HANDOFF), timeout=.5):
                    return True
            wait_for(ready, 'Trial handoff listener failed.')
            loopback_trial(payload, single_app.link_key(key), binary)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    print('Single-app trial / current Day8 adapter / fixed ChatGPT exit passed.', flush=True)


def udp_guard():
    def count():
        return int(admin('udp-count', output=True).stdout.strip())
    before = count()
    iface = catalog.uplink_index()
    if not iface:
        raise RuntimeError('Physical interface missing.')
    # TEST-NET, no user content. A physical-interface bound socket deliberately skips TUN.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IP, 25, iface)
        for _ in range(3):
            try:
                sock.sendto(b'day8-pf-test', ('192.0.2.222', 43127))
            except PermissionError:
                pass
    after = count()
    if after < before + 3:
        raise RuntimeError('No positive PF evidence for physical UDP blocking.')
    print('Physical-interface UDP blocked by PF (counter delta %d).' % (after-before), flush=True)


def accept():
    wait_for(lambda: interface('1.1.1.1') == 'utun63', 'Capture service did not take the default route.')
    report = runtime.control(HOME, 'verify-system-tun')
    # Only predefined hosts, rule names and booleans; never the trace body or exit IP.
    install.atomic(HOME / '.local/tun-check.json', json.dumps(report).encode())
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not report['ok']:
        raise RuntimeError('Transparent TUN route or fixed residential exit check failed.')
    udp_guard()
    if session('running', check=False) or session('login', check=False):
        raise RuntimeError('ClashX is still active or set to open at login.')


def restore_previous(state):
    if state.get('hadLogin'):
        session('enable-login')
    if not state.get('hadRunning', True):
        return
    profile = state.get('profile')
    core = state.get('core')
    if not profile or not core:
        # Read old migration records created before the single-app-only installer.
        settings = json.loads(install.STATE.read_text())
        profile, core = settings['source'], settings['core']
    original = Path(profile)
    if not original.is_file():
        raise RuntimeError('Original profile is missing; recovery is incomplete.')
    subprocess.run(['/usr/bin/defaults', 'write', install.svc.APP_ID,
                    'selectConfigName', '-string', original.stem], check=True)
    session('open')
    _, url, secret = core_settings(SimpleNamespace(core_config=Path(core)))
    wait_for(lambda: controller.request(url, secret, '/version'), 'Previous Clash controller unavailable.', 45)
    controller.request(url, secret, '/configs?force=true', {'path': str(original)}, 'PUT')
    wait_for(lambda: controller.request(url, secret, '/configs').get('tun', {}).get('enable'),
             'Previous Clash TUN did not restore.', 25)


def restore():
    state = json.loads(STATE.read_text())
    if state.get('prepared'):
        admin('stop')
    # Never fall back to an adapter-only login service, including after login.
    install.ctl('disable', install.TARGET)
    install.stop_agent()
    if state.get('prepared'):
        admin('remove')
    restore_previous(state)
    MARK.unlink(missing_ok=True)
    settings = json.loads(install.STATE.read_text())
    settings.update({'schema': 2, 'mode': 'single-app'})
    install.atomic(install.STATE, json.dumps(settings).encode())
    STATE.unlink()
    print('Gateway stopped. Previous network restored; compatibility service is not running.', flush=True)


def migrate(previous=None):
    if STATE.exists():
        raise RuntimeError('Migration already exists; use check or rollback.')
    status = runtime.control(HOME, 'status')
    if status['mode'] != 'standalone':
        check_ports()
    state = dict(previous if previous is not None else previous_session())
    state['prepared'] = False
    pkg = archive()
    install_binary(pkg)
    payload, vault_key = unlocked()
    key = single_app.link_key(vault_key)
    install.atomic(STATE, json.dumps(state).encode())
    progress = HOME / '.local/migration-result.json'
    current_stage = 'routing_update'
    try:
        # Legacy code is replaced only here, after preflight and administrator authorization.
        install.upgrade(migrating=True)
        install.atomic(MARK, b'1\n')
        loopback_trial(payload, key, HOME / 'mihomo')
        print('New routing core and authenticated handoff passed ChatGPT exit check.', flush=True)
        print('Installing native capture service and validating PF rules.', flush=True)
        current_stage = 'capture_prepare'
        admin('prepare', pkg, key=key)
        state['prepared'] = True
        install.atomic(STATE, json.dumps(state).encode())
        current_stage = 'udp_guard'
        admin('guard')
        udp_guard()
        if state.get('hadLogin'):
            session('disable-login')
        if state.get('hadRunning'):
            current_stage = 'previous_proxy_quit'
            print('Stopping previous ClashX and starting the system TUN.', flush=True)
            session('quit')
            wait_for(lambda: not session('running', check=False), 'ClashX did not quit.')
        current_stage = 'capture_start'
        admin('start')
        current_stage = 'system_acceptance'
        accept()
        current_stage = 'service_restart'
        agent()
        admin('restart')
        time.sleep(2)
        accept()
        settings = json.loads(install.STATE.read_text())
        settings.update({'schema': 2, 'mode': 'single-app'})
        install.atomic(install.STATE, json.dumps(settings).encode())
        install.atomic(progress, json.dumps({'stage': 'complete', 'ok': True}).encode())
        print('Single-app cutover and cold service recovery verified.', flush=True)
    except BaseException as exc:
        install.atomic(progress, json.dumps({'stage': current_stage, 'ok': False,
                                            'error': type(exc).__name__}).encode())
        print('Cutover incomplete; stopping gateway and restoring the previous network.', flush=True)
        restore()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preflight', 'migrate', 'rollback', 'check'])
    action = parser.parse_args().action
    if sys.platform != 'darwin' or os.geteuid() == 0:
        sys.exit('Run as the enrolled macOS login user.')
    if not install.STATE.is_file():
        sys.exit('Existing enrolled login service is required.')
    if action == 'preflight':
        return preflight()
    compile_session()
    if action == 'migrate':
        preflight()
    authorize()
    {'migrate': migrate, 'rollback': restore, 'check': accept}[action]()


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        # Parser and subprocess diagnostics may contain private values.
        if type(exc) is RuntimeError:
            print(str(exc) if str(exc) in ('No positive PF evidence for physical UDP blocking.',
                  'Transparent TUN route or fixed residential exit check failed.',
                  'Authenticated local handoff or residential exit verification failed.',
                  'Capture service did not take the default route.', 'Login agent startup failed.',
                  'Previous ClashX route failed to restore.') else 'Migration stage failed.', file=sys.stderr)
        else:
            print('Migration operation incomplete (' + type(exc).__name__ + ').', file=sys.stderr)
        sys.exit(1)
