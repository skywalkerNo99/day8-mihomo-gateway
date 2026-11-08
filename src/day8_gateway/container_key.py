"""Enroll a vault-specific AES key in the Linux container's private volume."""
import os
from pathlib import Path
import stat
import tempfile

from . import config, vault


KEY_FILE = Path('/var/lib/day8-unlock/vault.key')


def enroll(profile, password, path=KEY_FILE):
    payload, secret = vault.unlock_with_password(vault.read_envelope(profile), password)
    config.validate(payload)
    # The directory is a dedicated writable Docker volume during enrollment.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        pending = Path(tmp.name)
        try:
            tmp.write(secret)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_key(path=KEY_FILE):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise vault.VaultError('Unsafe unlock key permissions.')
        secret = f.read(33)
    return secret


def unlock(profile, path=KEY_FILE):
    return config.validate(vault.decrypt_with_key(vault.read_envelope(profile), read_key(path)))
