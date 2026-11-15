"""Install, upgrade or remove the macOS single-app login service."""
import argparse
import gzip
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from day8_gateway import macos_keychain, macos_service as svc, runtime
from day8_gateway.cli import password

HOME = Path.home()
SUPPORT = HOME / 'Library/Application Support/Day8Gateway'
JOB = HOME / 'Library/LaunchAgents' / (svc.LABEL + '.plist')
DOMAIN = f'gui/{os.getuid()}'
TARGET = DOMAIN + '/' + svc.LABEL
STATE = SUPPORT / 'installation.json'


def atomic(path, data):
    path.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def ctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, check=check)


def stop_agent():
    # bootout is asynchronous: a successful return does not release the label.
    # Reusing it immediately makes bootstrap fail with launchd error 5.
    ctl('bootout', TARGET, check=False)
    until = time.monotonic() + 25
    while time.monotonic() < until:
        if ctl('print', TARGET, check=False).returncode != 0:
            return
        time.sleep(.1)
    raise RuntimeError('Previous login agent did not unregister.')


def agent_job(adapter_port):
    return {'Label': svc.LABEL,
            'ProgramArguments': [str(SUPPORT / '.venv/bin/python'), '-m', 'day8_gateway.macos_service',
                                 '--root', str(SUPPORT), '--adapter-port', str(adapter_port)],
            'EnvironmentVariables': {'PYTHONPATH': str(SUPPORT / 'src')},
            'WorkingDirectory': str(SUPPORT), 'RunAtLoad': True, 'KeepAlive': True,
            'ThrottleInterval': 5, 'Umask': 0o077,
            'StandardErrorPath': str(SUPPORT / 'service.log')}


def install_catalog():
    source = ROOT / 'secrets/routes.enc.json'
    if source.is_file():
        atomic(SUPPORT / 'routes.enc.json', source.read_bytes())


def start_agent(adapter_port):
    atomic(JOB, plistlib.dumps(agent_job(adapter_port)))
    ctl('enable', TARGET)
    ctl('bootstrap', DOMAIN, str(JOB))
    for _ in range(100):
        try:
            state = runtime.control(SUPPORT, 'status')
            if state.get('mode') == 'standalone' and state['adapter_port'] == adapter_port:
                return
        except (OSError, RuntimeError):
            pass
        time.sleep(.25)
    raise RuntimeError('Login agent startup failed.')


def install(args):
    if STATE.exists() or JOB.exists():
        raise SystemExit('Existing installation: use gateway macos migrate or upgrade.')
    import macos_takeover as takeover
    takeover.compile_session()
    takeover.check_ports(include_adapter=args.adapter_port)
    previous = takeover.previous_session()
    profile = ROOT / 'secrets/deployment.enc.json'
    try:
        macos_keychain.unlock(profile)
    except RuntimeError:
        macos_keychain.enroll(profile, password('首次钥匙串登记密码（隐藏输入）: '))
    package = takeover.archive()
    SUPPORT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SUPPORT, 0o700)
    for name in ('src', 'rules', '.venv'):
        shutil.copytree(ROOT / name, SUPPORT / name, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    atomic(SUPPORT / 'deployment.enc.json', profile.read_bytes())
    atomic(SUPPORT / 'mihomo', gzip.decompress(package.read_bytes()))
    (SUPPORT / 'mihomo').chmod(0o700)
    install_catalog()
    atomic(STATE, json.dumps({'schema': 2, 'mode': 'single-app'}).encode())
    try:
        start_agent(args.adapter_port)
        takeover.preflight()
        takeover.authorize()
        takeover.migrate(previous=previous)
    except BaseException:
        # A prepared migration owns its own recovery. Before that point only the
        # loopback service was installed, so stopping it leaves the old route intact.
        if not takeover.STATE.exists():
            stop_agent()
            ctl('disable', TARGET, check=False)
        raise
    print('Installed single-app TUN; system routing and ChatGPT exit verified.')


def remove(args):
    import macos_takeover as takeover
    if takeover.STATE.exists():
        takeover.compile_session()
        takeover.authorize()
        takeover.restore()
    settings = json.loads(STATE.read_text())
    if settings.get('schema') != 2:
        raise SystemExit('Migrate the legacy installation before removing it.')
    stop_agent()
    JOB.unlink(missing_ok=True)
    STATE.unlink()
    (SUPPORT / 'single-app.enabled').unlink(missing_ok=True)
    for name in ('src', 'rules', '.venv'):
        shutil.rmtree(SUPPORT / name, ignore_errors=True)
    for name in ('deployment.enc.json', 'routes.enc.json', 'routes.local.enc.json', 'mihomo'):
        (SUPPORT / name).unlink(missing_ok=True)
    for name in ('node.enc', 'residual.enc', 'domain-rules.enc', 'probe-settings.json', 'probe-results.json', 'lan-proxy.json'):
        (SUPPORT / '.local' / name).unlink(missing_ok=True)
    print('Single-app services removed. Previous network restored when recorded.')


def upgrade(migrating=False):
    if not STATE.is_file() or not JOB.is_file():
        raise SystemExit('Install the single-app login service first.')
    settings = json.loads(STATE.read_text())
    if not migrating and settings.get('mode') != 'single-app':
        raise SystemExit('Legacy installation: run gateway macos migrate first.')
    old_job = JOB.read_bytes()
    previous = runtime.control(SUPPORT, 'status')
    port = previous['adapter_port']
    with tempfile.TemporaryDirectory(dir=SUPPORT) as temp:
        stage = Path(temp)
        targets = ('src', 'rules')
        for name in targets:
            shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns('__pycache__'))
        replaced = []
        try:
            # Rewrite launchd arguments as well as code: old jobs contain Clash paths.
            stop_agent()
            for name in targets:
                os.rename(SUPPORT / name, stage / ('old-' + name))
                replaced.append(name)
                os.rename(stage / name, SUPPORT / name)
            start_agent(port)
        except BaseException:
            stop_agent()
            for name in reversed(replaced):
                shutil.rmtree(SUPPORT / name, ignore_errors=True)
                os.rename(stage / ('old-' + name), SUPPORT / name)
            atomic(JOB, old_job)
            ctl('enable', TARGET)
            ctl('bootstrap', DOMAIN, str(JOB))
            raise
    if not migrating:
        install_catalog()
    print('Updated the single-app login service and launchd arguments.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'upgrade', 'remove'])
    parser.add_argument('--adapter-port', type=int, default=17899)
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.geteuid() == 0:
        parser.error('Run as the macOS login user.')
    if not 1 <= args.adapter_port <= 65535:
        parser.error('Invalid adapter port.')
    os.umask(0o077)
    if args.action == 'install':
        install(args)
    elif args.action == 'upgrade':
        upgrade()
    else:
        remove(args)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        sys.exit('Operation incomplete (' + type(exc).__name__ + '). Check the login agent and migration state.')
