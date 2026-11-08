"""Run Muon and restore the unlocked policy when Clash loads its startup profile."""
import argparse
import asyncio
import logging
from pathlib import Path

from . import config, controller, macos_keychain, node_updates, runtime, scheduled_probes, single_app
from .cli import core_settings, read_yaml

LOCK = 'Day8-Startup-Locked'
PROFILE = 'Day8-Residential'
LABEL = 'io.day8gateway.macos'
APP_ID = 'com.metacubex.ClashX.meta'


def startup_config(base, url, secret):
    cfg = {k: base[k] for k in ('mixed-port', 'port', 'socks-port', 'ipv6') if k in base}
    cfg.update({'mode': 'rule', 'allow-lan': False, 'external-controller': url.removeprefix('http://'),
                'secret': secret, 'tun': {**(base.get('tun') or {}), 'enable': True},
                'proxy-groups': [{'name': LOCK, 'type': 'select', 'proxies': ['REJECT']}],
                'rules': [f'MATCH,{LOCK}']})
    return cfg


def restore_if_locked(payload, args, reconcile=False):
    _, url, secret = core_settings(args)
    rules = controller.request(url, secret, '/rules').get('rules') or []
    locked = len(rules) == 1 and rules[0].get('proxy') == LOCK and rules[0].get('type', '').lower() == 'match'
    managed = [r.get('proxy') for r in rules[-2:]] == [config.TRANSPORT, 'REJECT']
    if not locked and not (reconcile and managed):
        return False
    apply_policy(payload, args, url, secret)
    return True


def apply_policy(payload, args, url, secret):
    cfg = config.macos(payload, read_yaml(args.profile), args.root / 'rules', args.adapter_port)
    cfg['external-controller'] = url.removeprefix('http://')
    cfg['secret'] = secret
    controller.apply(url, secret, cfg)
    return True


async def supervise(payload, args):
    async def watch():
        first = True
        while True:
            try:
                await asyncio.to_thread(restore_if_locked, payload, args, first)
                first = False
            except (OSError, ValueError, KeyError):
                pass  # The GUI and its controller can start after the login agent.
            await asyncio.sleep(2)

    args.probes = scheduled_probes.ProbeSchedule(args.root, args.update_key)
    timer = asyncio.create_task(args.probes.run())
    monitor = None
    try:
        if (args.root / 'single-app.enabled').is_file():
            args.port = single_app.PORT
            args.controller_port = single_app.CONTROLLER
            args.mihomo = args.root / 'mihomo'
            args.uplink = single_app.Uplink()
            cfg = single_app.routing(payload, args.update_key, args.adapter_port)
        else:
            cfg = None
            monitor = asyncio.create_task(watch())
        await runtime.serve(payload, cfg, args)
    finally:
        tasks = [timer] if monitor is None else [timer, monitor]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--core-config', type=Path, required=True)
    parser.add_argument('--adapter-port', type=int, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        args.vault = args.root / 'deployment.enc.json'
        payload = macos_keychain.unlock(args.vault)
        args.update_key = macos_keychain.read_key(args.vault)
        node_updates.load_saved(args.root, args.vault, args.update_key, payload)
        asyncio.run(supervise(payload, args))
    except Exception:
        raise SystemExit('Mac gateway could not start. Verify keychain access and listener availability.') from None


if __name__ == '__main__':
    main()
