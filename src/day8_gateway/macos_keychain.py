"""Store the vault-specific unlock key in the macOS user's default keychain."""
import ctypes as C
import hashlib
from pathlib import Path

from . import config, vault

SERVICE = b'io.day8gateway.macos.vault-key'


def _security():
    lib = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
    ptr, size = C.c_void_p, C.c_uint32
    lib.SecKeychainAddGenericPassword.argtypes = [ptr, size, ptr, size, ptr, size, ptr, ptr]
    lib.SecKeychainFindGenericPassword.argtypes = [ptr, size, ptr, size, ptr, C.POINTER(size), C.POINTER(ptr), ptr]
    lib.SecKeychainItemFreeContent.argtypes = [ptr, ptr]
    lib.SecKeychainSetUserInteractionAllowed.argtypes = [C.c_ubyte]
    return lib


def _identity(profile):
    # A newly sealed vault receives its own keychain entry.
    return hashlib.sha256(Path(profile).read_bytes()).hexdigest().encode('ascii')


def enroll(profile, password):
    payload, key = vault.unlock_with_password(vault.read_envelope(profile), password)
    config.validate(payload)
    account = _identity(profile)
    lib = _security()
    lib.SecKeychainSetUserInteractionAllowed(True)
    status = lib.SecKeychainAddGenericPassword(None, len(SERVICE), SERVICE, len(account), account,
                                              len(key), key, None)
    if status not in (0, -25299):  # errSecDuplicateItem on an idempotent reinstall.
        raise RuntimeError(f'Keychain enrollment failed (OSStatus {status}).')
    unlock(profile)


def read_key(profile):
    account = _identity(profile)
    lib = _security()
    # launchd retries after login if the keychain is still locked.
    lib.SecKeychainSetUserInteractionAllowed(False)
    size, data = C.c_uint32(), C.c_void_p()
    status = lib.SecKeychainFindGenericPassword(None, len(SERVICE), SERVICE, len(account), account,
                                               C.byref(size), C.byref(data), None)
    if status:
        raise RuntimeError(f'Keychain locked or not enrolled (OSStatus {status}).')
    try:
        if size.value != 32:
            raise vault.VaultError('Invalid keychain entry.')
        secret = C.string_at(data, size.value)
    finally:
        lib.SecKeychainItemFreeContent(None, data)
    return secret


def unlock(profile):
    return config.validate(vault.decrypt_with_key(vault.read_envelope(profile), read_key(profile)))
