"""Run the macOS single-app routing core and Muon under the login agent."""
import argparse
import asyncio
import logging
from pathlib import Path

from . import macos_keychain, node_updates, residual, runtime, scheduled_probes, single_app

LABEL = 'io.day8gateway.macos'
# Used only to identify the previous application during migration and recovery.
APP_ID = 'com.metacubex.ClashX.meta'


async def supervise(payload, args):
    args.port = single_app.PORT
    args.controller_port = single_app.CONTROLLER
    args.mihomo = args.root / 'mihomo'
    args.uplink = single_app.Uplink()
    cfg = single_app.routing(payload, args.update_key, args.adapter_port)
    args.probes = scheduled_probes.ProbeSchedule(args.root, args.update_key)
    timer = asyncio.create_task(args.probes.run())
    try:
        await runtime.serve(payload, cfg, args)
    finally:
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--adapter-port', type=int, default=17899)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        args.vault = args.root / 'deployment.enc.json'
        payload = macos_keychain.unlock(args.vault)
        args.update_key = macos_keychain.read_key(args.vault)
        residual.load(args.root, args.vault, args.update_key, payload)
        node_updates.load_saved(args.root, args.vault, args.update_key, payload)
        asyncio.run(supervise(payload, args))
    except Exception:
        raise SystemExit('Mac gateway could not start. Verify keychain access and listener availability.') from None


if __name__ == '__main__':
    main()
