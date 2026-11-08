import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography.exceptions import InvalidTag
from day8_gateway import residual
from test_vault_config import fixture


class ResidualTests(unittest.TestCase):
    def setUp(self):
        self.base = fixture()
        self.key = bytes(32)
        view = residual.form(self.base, self.key)
        self.edit = dict(view['residual'], password='', revision=view['revision'])

    def test_form_omits_secret_and_blank_password_keeps_it(self):
        view = residual.form(self.base, self.key)
        self.assertNotIn('password', view['residual'])
        self.assertNotIn(self.base['residual']['password'], json.dumps(view))
        self.assertEqual(residual.candidate(self.base, self.key, self.edit), self.base)
        for patch in [{'port': 1081}, {'server': 'different.test'}, {'port': 0}, {'port': True},
                      {'revision': 'stale'}, {'server': 'https://host.test/'}, {'expectedExit': 'wrong'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                residual.candidate(self.base, self.key, {**self.edit, **patch})

    def test_encrypted_override_is_bound_to_base_and_survives_restart(self):
        new = residual.candidate(self.base, self.key, {**self.edit, 'server': 'host.example',
            'password': 'new-test-pass', 'expectedExit': '203.0.113.1'})
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / 'base'
            vault.write_bytes(b'fixture envelope')
            residual.save(root, vault, self.key, new)
            data = (root / '.local/residual.enc').read_bytes()
            for sensitive in [b'new-test-pass', b'host.example', b'203.0.113.1']:
                self.assertNotIn(sensitive, data)
            restored = copy.deepcopy(self.base)
            residual.load(root, vault, self.key, restored)
            self.assertEqual(new, restored)
            self.assertEqual(restored['day8'], self.base['day8'])
            with self.assertRaises(ValueError):
                residual.candidate(restored, self.key, self.edit)
            vault.write_bytes(b'different vault')
            with self.assertRaises(InvalidTag):
                residual.load(root, vault, self.key, restored)


class ResidualTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_update_and_failed_reload_preserve_policy_and_disk(self):
        import asyncio
        from contextlib import ExitStack
        import socket
        from types import SimpleNamespace
        from unittest.mock import patch
        from day8_gateway import runtime, single_app, verify

        core = Path(__file__).resolve().parents[1] / '.tools/mihomo'
        if not core.exists():
            self.skipTest('Local mihomo bootstrap required for live reload test')
        with TemporaryDirectory(dir='/tmp') as tmp:
            root = Path(tmp)
            base = root / 'base'
            base.write_bytes(b'synthetic base')
            with ExitStack() as stack:
                sockets = [stack.enter_context(socket.socket()) for _ in range(4)]
                for sock in sockets:
                    sock.bind(('127.0.0.1', 0))
                port, adapter, api, handoff = [s.getsockname()[1] for s in sockets]

            key = bytes(32)
            payload = fixture()
            cfg = single_app.routing(payload, key, adapter)
            cfg.update({'mixed-port': port, 'external-controller': f'127.0.0.1:{api}'})
            cfg['listeners'][0]['port'] = handoff
            args = SimpleNamespace(root=root, vault=base, update_key=key, uplink=single_app.Uplink(),
                                   port=port, adapter_port=adapter, controller_port=api, mihomo=core)
            async def call(cmd, **kwargs):
                return await asyncio.to_thread(runtime.control, root, cmd, **kwargs)
            task = asyncio.create_task(runtime.serve(payload, cfg, args))
            try:
                for _ in range(100):
                    if task.done():
                        await task
                    if (root / '.local/control.sock').exists():
                        break
                    await asyncio.sleep(.05)
                original = await call('get-residual')
                edit = dict(original['residual'], revision=original['revision'], password='updated-test-password')
                with patch.object(residual, 'test_exit', return_value=True), patch.object(verify, 'check', return_value={'ok': True}):
                    self.assertTrue((await call('save-residual', edit=edit))['ok'])
                restored = fixture()
                residual.load(root, base, key, restored)
                self.assertEqual(restored['residual']['password'], edit['password'])
                self.assertEqual(restored, payload)
                before = (root / '.local/residual.enc').read_bytes()
                current = await call('get-residual')
                self.assertNotEqual(current['revision'], original['revision'])
                with patch.object(residual, 'test_exit', return_value=True), patch.object(verify, 'check', return_value={'ok': False}):
                    failed = await call('save-residual', edit={**edit, 'revision': current['revision'], 'password': 'bad-test'})
                    self.assertFalse(failed['ok'])
                policy = await call('domain-rules')
                self.assertTrue((await call('change-domain-rule', revision=policy['revision'], index=-1,
                                            action='save', rule={'kind':'exact','value':'example.test','target':'REJECT'}))['ok'])
                live = next(p for p in cfg['proxies'] if p['name'] == 'Residual-egress')
                self.assertEqual(live['password'], edit['password'])
                self.assertEqual(await call('get-residual'), current)
                self.assertEqual((root / '.local/residual.enc').read_bytes(), before)
            finally:
                if not task.done() and (root / '.local/control.sock').exists():
                    await call('stop')
                await asyncio.wait_for(task, 10)
