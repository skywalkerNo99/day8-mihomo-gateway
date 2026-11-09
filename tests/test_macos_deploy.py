from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from day8_gateway import cli

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import macos_persistent as install


class MacDeployTests(unittest.TestCase):
    def test_legacy_temporary_deploy_is_rejected_before_unlock_or_mutation(self):
        argv = ['gateway', 'macos', 'deploy', '--profile', '/synthetic/profile',
                '--core-config', '/synthetic/core', '--block-public-udp', '--apply']
        with (patch('sys.argv', argv), patch.object(cli, 'password') as password,
              patch.object(cli.runtime, 'start') as start, redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit) as exc:
                cli.main()
        self.assertEqual(exc.exception.code, 2)
        password.assert_not_called()
        start.assert_not_called()

    def test_macos_install_needs_no_clash_profile(self):
        with (patch('sys.argv', ['gateway', 'macos', 'install']),
              patch.object(cli.subprocess, 'run') as run, redirect_stdout(io.StringIO())):
            run.return_value.returncode = 0
            self.assertEqual(cli.main(), 0)
        self.assertEqual(run.call_args.args[0][-2:], ['scripts/macos_persistent.py', 'install'])

    def test_login_job_has_no_clash_configuration_dependency(self):
        job = install.agent_job(17899)
        self.assertNotIn('--profile', job['ProgramArguments'])
        self.assertNotIn('--core-config', job['ProgramArguments'])
        self.assertTrue(job['KeepAlive'] and job['RunAtLoad'])
