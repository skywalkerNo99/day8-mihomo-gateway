import stat
import tempfile
import unittest
from pathlib import Path

from day8_gateway import container_key, vault
from test_vault_config import fixture


class ContainerKeyTests(unittest.TestCase):
    def test_restart_rotation_and_failed_enrollment(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'deployment.enc.json'
            path = Path(tmp) / 'volume' / 'vault.key'
            vault.save(profile, fixture(), 'first-test-password')
            container_key.enroll(profile, 'first-test-password', path)
            self.assertEqual(container_key.unlock(profile, path), fixture())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(vault.VaultError):
                container_key.enroll(profile, 'wrong-test-password', path)
            self.assertEqual(container_key.unlock(profile, path), fixture())

            vault.save(profile, fixture(), 'second-test-password')
            with self.assertRaises(vault.VaultError):
                container_key.unlock(profile, path)
            container_key.enroll(profile, 'second-test-password', path)
            self.assertEqual(container_key.unlock(profile, path), fixture())
            path.chmod(0o644)
            with self.assertRaises(vault.VaultError):
                container_key.unlock(profile, path)
