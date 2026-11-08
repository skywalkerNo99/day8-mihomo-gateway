"""Install or remove the login-time Day8 policy for ClashX Meta."""
import argparse
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
from day8_gateway import controller, macos_keychain, macos_service as svc, runtime
from day8_gateway.cli import core_settings, password, read_yaml

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
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def ctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, check=check)


def select(name):
    subprocess.run(['/usr/bin/defaults', 'write', svc.APP_ID, 'selectConfigName', '-string', name], check=True)


def install_catalog():
    source = ROOT / 'secrets/routes.enc.json'
    if source.is_file():
        atomic(SUPPORT / 'routes.enc.json', source.read_bytes())


def install(args):
    if STATE.exists() or JOB.exists() or args.locked.exists():
        raise SystemExit('An installation or profile already exists. Inspect it before reinstalling.')
    prefs = subprocess.check_output(['/usr/bin/defaults', 'export', svc.APP_ID, '-'])
    current = plistlib.loads(prefs).get('selectConfigName')
    if current != args.profile.stem:
        raise SystemExit('The source profile must match the currently selected ClashX profile.')
    profile = ROOT / 'secrets/deployment.enc.json'
    try:
        macos_keychain.unlock(profile)
    except RuntimeError:
        macos_keychain.enroll(profile, password('首次钥匙串登记密码（隐藏输入）: '))
    core, url, secret = core_settings(args)
    active = controller.request(url, secret, '/configs')
    if not active.get('tun', {}).get('enable') or active.get('mode') != 'rule':
        raise SystemExit('Enable ClashX TUN and rule mode before installing.')
    core.update(read_yaml(args.profile))
    core['mixed-port'] = active['mixed-port']
    locked = svc.startup_config(core, url, secret)
    SUPPORT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SUPPORT, 0o700)
    for name in ('src', 'rules', '.venv'):
        shutil.copytree(ROOT / name, SUPPORT / name, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    atomic(SUPPORT / 'deployment.enc.json', profile.read_bytes())
    install_catalog()
    atomic(args.locked, json.dumps(locked, ensure_ascii=False).encode())
    settings = {'source': str(args.profile), 'locked': str(args.locked), 'core': str(args.core_config)}
    atomic(STATE, json.dumps(settings).encode())
    python = str(SUPPORT / '.venv/bin/python')
    job = {'Label': svc.LABEL, 'ProgramArguments': [python, '-m', 'day8_gateway.macos_service',
           '--root', str(SUPPORT), '--profile', str(args.locked), '--core-config', str(args.core_config),
           '--adapter-port', str(args.adapter_port)], 'EnvironmentVariables': {'PYTHONPATH': str(SUPPORT / 'src')},
           'WorkingDirectory': str(SUPPORT), 'RunAtLoad': True, 'KeepAlive': True,
           'ThrottleInterval': 5, 'Umask': 0o077,
           'StandardErrorPath': str(SUPPORT / 'service.log')}
    atomic(JOB, plistlib.dumps(job))
    ctl('bootstrap', DOMAIN, str(JOB))
    for _ in range(80):
        try:
            if runtime.control(SUPPORT, 'status')['adapter_port'] == args.adapter_port:
                break
        except (OSError, RuntimeError):
            pass
        time.sleep(.25)
    else:
        raise SystemExit('Agent did not start. Existing live policy was not switched. Use remove before retrying.')
    # The on-disk selection is committed only after launchd can unlock and bind.
    select(args.locked.stem)
    controller.request(url, secret, '/configs?force=true', {'path': str(args.locked)}, 'PUT')
    for _ in range(80):
        rules = controller.request(url, secret, '/rules')['rules']
        if len(rules) > 2 and rules[-2]['proxy'] == 'Day8-native':
            break
        time.sleep(.25)
    else:
        raise SystemExit('Startup policy remains locked. Check the login agent.')
    verified = runtime.control(SUPPORT, 'verify', proxy=f'http://127.0.0.1:{active["mixed-port"]}')
    if not verified['ok']:
        raise SystemExit('Residential exit did not verify. Inspect the installed policy or use remove.')
    print('Installed. Startup policy loaded and three ChatGPT exit checks passed.')


def remove(args):
    if (SUPPORT / 'single-app.enabled').exists():
        raise SystemExit('Restore the single-app TUN first using scripts/macos_takeover.py rollback.')
    settings = json.loads(STATE.read_text())
    args.core_config = Path(settings['core'])
    _, url, secret = core_settings(args)
    original = Path(settings['source'])
    if not original.is_file():
        raise SystemExit('Original profile is missing.')
    select(original.stem)
    controller.request(url, secret, '/configs?force=true', {'path': str(original)}, 'PUT')
    ctl('bootout', TARGET, check=False)
    JOB.unlink(missing_ok=True)
    Path(settings['locked']).unlink(missing_ok=True)
    STATE.unlink()
    # Copied vault and code are private; remove only installer-owned files.
    for name in ('src', 'rules', '.venv'):
        shutil.rmtree(SUPPORT / name, ignore_errors=True)
    (SUPPORT / 'deployment.enc.json').unlink(missing_ok=True)
    (SUPPORT / 'routes.enc.json').unlink(missing_ok=True)
    (SUPPORT / 'routes.local.enc.json').unlink(missing_ok=True)
    for name in ('node.enc', 'probe-settings.json', 'probe-results.json'):
        (SUPPORT / '.local' / name).unlink(missing_ok=True)
    print('Original profile restored. LaunchAgent removed.')


def upgrade():
    if not STATE.is_file() or not JOB.is_file():
        raise SystemExit('Install the login service first.')
    with tempfile.TemporaryDirectory(dir=SUPPORT) as temp:
        stage = Path(temp)
        targets = ('src', 'rules')
        for name in targets:
            shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns('__pycache__'))
        replaced = []
        try:
            previous = runtime.control(SUPPORT, 'status')['pid']
            for name in targets:
                os.rename(SUPPORT / name, stage / ('old-' + name))
                replaced.append(name)
                os.rename(stage / name, SUPPORT / name)
            ctl('kickstart', '-k', TARGET)
            for _ in range(100):
                try:
                    state = runtime.control(SUPPORT, 'status')
                    if state.get('ok') and state.get('pid') != previous:
                        break
                except (RuntimeError, OSError):
                    pass
                time.sleep(.25)
            else:
                raise RuntimeError('Updated agent did not start.')
        except BaseException:
            for name in reversed(replaced):
                shutil.rmtree(SUPPORT / name, ignore_errors=True)
                os.rename(stage / ('old-' + name), SUPPORT / name)
            if replaced:
                ctl('kickstart', '-k', TARGET)
            raise
    install_catalog()
    print('Updated the installed login service.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['install', 'upgrade', 'remove'])
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--core-config', type=Path)
    parser.add_argument('--adapter-port', type=int, default=17899)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == 'install':
            if not args.profile or not args.core_config:
                parser.error('install requires --profile and --core-config')
            args.profile = args.profile.expanduser().resolve()
            args.core_config = args.core_config.expanduser().resolve()
            args.locked = args.profile.with_name(svc.PROFILE + '.yaml')
            install(args)
        elif args.action == 'upgrade':
            upgrade()
        else:
            remove(args)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        sys.exit('Operation incomplete (' + type(exc).__name__ + '). Check the login agent and controller.')
