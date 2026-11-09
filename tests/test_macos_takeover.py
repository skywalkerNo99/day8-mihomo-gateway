from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import macos_persistent as install
import macos_takeover as takeover


class MigrationTests(unittest.TestCase):
    def test_failed_system_check_keeps_safe_route_evidence_before_rollback(self):
        report = {'ok': False, 'routes': [{'host': 'chatgpt.com', 'ok': False}]}
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(takeover, 'HOME', Path(tmp)),
                  patch.object(takeover, 'interface', return_value='utun63'),
                  patch.object(takeover.runtime, 'control', return_value=report),
                  self.assertRaises(RuntimeError)):
                takeover.accept()
            self.assertEqual(json.loads((Path(tmp)/'.local/tun-check.json').read_text()), report)

    def test_fresh_install_without_clash_needs_no_preferences_or_controller(self):
        with (patch.object(takeover, 'session', return_value=False),
              patch.object(takeover.socket, 'socket'),
              patch.object(takeover.subprocess, 'check_output') as read,
              patch.object(takeover.controller, 'request') as api):
            self.assertEqual(takeover.previous_session(), {'hadRunning': False, 'hadLogin': False})
        read.assert_not_called()
        api.assert_not_called()

    def test_rollback_stops_gateway_and_restores_original_profile_without_adapter_mode(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            state = root / 'migration.json'
            state.write_text(json.dumps({'prepared': True, 'hadRunning': True, 'hadLogin': True,
                                         'profile': str(root/'original.yaml'), 'core': str(root/'core.yaml')}))
            installation = root / 'installation.json'
            installation.write_text('{}')
            for owner, name, value in [(takeover, 'STATE', state), (takeover, 'MARK', root/'mark'),
                                        (install, 'STATE', installation)]:
                stack.enter_context(patch.object(owner, name, value))
            admin = stack.enter_context(patch.object(takeover, 'admin'))
            ctl = stack.enter_context(patch.object(install, 'ctl'))
            restore = stack.enter_context(patch.object(takeover, 'restore_previous'))
            old_agent = stack.enter_context(patch.object(takeover, 'agent'))
            takeover.restore()
            ctl.assert_any_call('disable', install.TARGET)
            ctl.assert_any_call('bootout', install.TARGET, check=False)
            old_agent.assert_not_called()
            restore.assert_called_once()
            self.assertEqual(admin.call_args_list[-1].args, ('remove',))
            self.assertFalse(state.exists())

    def test_failed_trial_restores_network_before_clash_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            for owner, name, value in [(takeover, 'STATE', root/'migration.json'),
                                        (takeover, 'MARK', root/'mark'), (takeover, 'HOME', root)]:
                stack.enter_context(patch.object(owner, name, value))
            for name in ('check_ports', 'agent', 'accept', 'udp_guard'):
                stack.enter_context(patch.object(takeover, name))
            stack.enter_context(patch.object(takeover, 'archive', return_value=root/'core.gz'))
            stack.enter_context(patch.object(takeover, 'install_binary'))
            stack.enter_context(patch.object(takeover, 'unlocked', return_value=({}, bytes(32))))
            stack.enter_context(patch.object(takeover.runtime, 'control', return_value={'mode': 'adapter'}))
            stack.enter_context(patch.object(install, 'upgrade'))
            stack.enter_context(patch.object(takeover, 'loopback_trial', side_effect=RuntimeError('trial failed')))
            restore = stack.enter_context(patch.object(takeover, 'restore'))
            session = stack.enter_context(patch.object(takeover, 'session'))
            admin = stack.enter_context(patch.object(takeover, 'admin'))
            with self.assertRaises(RuntimeError):
                takeover.migrate(previous={'hadRunning': True, 'hadLogin': True})
            restore.assert_called_once()
            session.assert_not_called()
            admin.assert_not_called()

    def test_existing_standalone_preflight_reuses_handoff_listener(self):
        with (patch.object(takeover, 'unlocked', return_value=({'test': True}, bytes(32))),
              patch.object(takeover.runtime, 'control', return_value={'mode': 'standalone', 'adapter_port': 17899}),
              patch.object(takeover, 'loopback_trial') as trial,
              patch.object(takeover.subprocess, 'Popen') as spawn):
            takeover.preflight()
        trial.assert_called_once()
        spawn.assert_not_called()


class UpgradeTests(unittest.TestCase):
    def test_waits_for_launchd_to_unregister_old_service_before_reloading(self):
        replies = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                   subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 113)]
        with (patch.object(install, 'ctl', side_effect=replies) as ctl,
              patch.object(install.time, 'sleep') as sleep):
            install.stop_agent()
        self.assertEqual(ctl.call_args_list[0].args, ('bootout', install.TARGET))
        self.assertEqual(ctl.call_args_list[-1].args, ('print', install.TARGET))
        self.assertEqual(sleep.call_count, 2)

    def test_failed_start_restores_code_rules_and_original_launch_job(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            repo, support = root/'repo', root/'support'
            for name in ('src', 'rules'):
                (repo/name).mkdir(parents=True)
                (repo/name/'version').write_text('new')
                (support/name).mkdir(parents=True)
                (support/name/'version').write_text('old')
            state, job = support/'installation.json', support/'job.plist'
            state.write_text(json.dumps({'schema': 2, 'mode': 'single-app'}))
            job.write_bytes(b'original launch job')
            for name, value in [('ROOT', repo), ('SUPPORT', support), ('STATE', state), ('JOB', job)]:
                stack.enter_context(patch.object(install, name, value))
            stack.enter_context(patch.object(install.runtime, 'control', return_value={'adapter_port': 17899}))
            stack.enter_context(patch.object(install, 'ctl'))
            stack.enter_context(patch.object(install, 'start_agent', side_effect=RuntimeError('synthetic failure')))
            with self.assertRaises(RuntimeError):
                install.upgrade()
            for name in ('src', 'rules'):
                self.assertEqual((support/name/'version').read_text(), 'old')
            self.assertEqual(job.read_bytes(), b'original launch job')
