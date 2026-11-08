"""Migrate an enrolled macOS login agent to its own TUN, or restore ClashX."""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
import time

import macos_persistent as install
from day8_gateway import catalog, macos_keychain, runtime, single_app, verify
from day8_gateway.tun_verify import interface

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


def agent(mode):
    install.ctl('kickstart', '-k', install.TARGET)
    wait_for(lambda: runtime.control(HOME, 'status')['mode'] == mode, 'Login agent startup failed.')


def compile_session():
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
    vault = HOME / 'deployment.enc.json'
    payload = macos_keychain.unlock(vault)
    key = macos_keychain.read_key(vault)
    adapter_port = runtime.control(HOME, 'status')['adapter_port']
    cfg = single_app.routing(payload, key, adapter_port)
    binary = ROOT / '.tools/mihomo'
    with tempfile.TemporaryDirectory() as folder:
        with subprocess.Popen([str(binary), '-d', folder, '-f', '/dev/stdin'], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
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
    print('Loopback capture / routing / current Day8 adapter / fixed ChatGPT exit passed.', flush=True)


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
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not report['ok']:
        raise RuntimeError('Transparent TUN route or fixed residential exit check failed.')
    udp_guard()
    if session('running', check=False) or session('login', check=False):
        raise RuntimeError('ClashX is still active or set to open at login.')


def restore():
    state = json.loads(STATE.read_text())
    if state.get('prepared'):
        admin('stop')
    MARK.unlink(missing_ok=True)
    agent('adapter')
    if state.get('hadLogin'):
        session('enable-login')
    session('open')
    wait_for(lambda: runtime.control(HOME, 'verify')['ok'], 'Previous ClashX route failed to restore.', 65)
    if state.get('prepared'):
        admin('remove')
    STATE.unlink()
    print('Previous ClashX + Day8 routing restored and verified.', flush=True)


def migrate():
    if STATE.exists() or MARK.exists():
        raise RuntimeError('Migration already exists; use check or rollback.')
    if not session('running', check=False) or runtime.control(HOME, 'status')['mode'] != 'adapter':
        raise RuntimeError('Start with the enrolled ClashX login service running.')
    if 'utun63' in subprocess.check_output(['/sbin/ifconfig','-l'], text=True).split():
        raise RuntimeError('utun63 already in use.')
    # Existing listeners must never be displaced.
    with ExitStack() as stack:
        for port in (single_app.PORT, single_app.CONTROLLER, single_app.HANDOFF):
            probe = stack.enter_context(socket.socket())
            probe.bind(('127.0.0.1', port))
    pkg = archive()
    install.atomic(HOME / 'mihomo', gzip.decompress(pkg.read_bytes()))
    (HOME / 'mihomo').chmod(0o700)
    vault = HOME / 'deployment.enc.json'
    payload = macos_keychain.unlock(vault)
    key = single_app.link_key(macos_keychain.read_key(vault))
    state = {'hadLogin': session('login', check=False), 'prepared': False}
    install.atomic(STATE, json.dumps(state).encode())
    try:
        install.atomic(MARK, b'1\n')
        agent('standalone')
        loopback_trial(payload, key, HOME / 'mihomo')
        print('New routing core and authenticated handoff passed ChatGPT exit check.', flush=True)
        print('Installing native capture service and validating PF rules.', flush=True)
        admin('prepare', pkg, key=key)
        state['prepared'] = True
        install.atomic(STATE, json.dumps(state).encode())
        admin('guard')
        udp_guard()
        if state['hadLogin']:
            session('disable-login')
        print('Stopping ClashX and starting the system TUN.', flush=True)
        session('quit')
        wait_for(lambda: not session('running', check=False), 'ClashX did not quit.')
        admin('start')
        accept()
        # Exercise both startup jobs with the permanent configuration.
        agent('standalone')
        admin('restart')
        time.sleep(2)
        accept()
        print('Single-app cutover and cold service recovery verified.', flush=True)
    except BaseException:
        print('Cutover incomplete; restoring the previous route.', flush=True)
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
    subprocess.run(['/usr/bin/sudo','-v'], check=True)
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
