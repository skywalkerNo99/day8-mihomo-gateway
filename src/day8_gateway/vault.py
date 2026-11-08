"""Versioned password-encrypted profile; never writes decrypted configuration."""
import base64
import json
import os
from pathlib import Path
import secrets
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

FORMAT = 'day8-mihomo-vault'
MAX_BYTES = 2_000_000
PARAMS = {'name':'argon2id','memory_kib':65536,'iterations':3,'lanes':4}

class VaultError(ValueError):
    pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')

def b64(raw):return base64.b64encode(raw).decode('ascii')
def decode(value):return base64.b64decode(value,validate=True)

def key(password,salt):
    if not isinstance(password,str) or not password:
        raise VaultError('The password must not be empty.')
    return Argon2id(salt=salt,length=32,iterations=PARAMS['iterations'],lanes=PARAMS['lanes'],memory_cost=PARAMS['memory_kib']).derive(password.encode('utf-8'))

def encrypt(payload,password):
    salt=secrets.token_bytes(16);nonce=secrets.token_bytes(12)
    header={'format':FORMAT,'version':1,'kdf':dict(PARAMS),'cipher':'aes-256-gcm','salt':b64(salt),'nonce':b64(nonce)}
    ciphertext=AESGCM(key(password,salt)).encrypt(nonce,canonical(payload),canonical(header))
    return {**header,'ciphertext':b64(ciphertext)}

def _cipher_parts(envelope):
    try:
        if set(envelope) != {'format','version','kdf','cipher','salt','nonce','ciphertext'}:
            raise VaultError('Unsupported vault fields.')
        if envelope['format'] != FORMAT or envelope['version'] != 1 or envelope['kdf'] != PARAMS or envelope['cipher'] != 'aes-256-gcm':
            raise VaultError('Unsupported vault format or KDF parameters.')
        salt = decode(envelope['salt'])
        nonce = decode(envelope['nonce'])
        ciphertext = decode(envelope['ciphertext'])
        if len(salt) != 16 or len(nonce) != 12 or not 16 <= len(ciphertext) <= MAX_BYTES:
            raise VaultError('Invalid vault lengths.')
        header = {k: v for k, v in envelope.items() if k != 'ciphertext'}
        return salt, nonce, ciphertext, canonical(header)
    except (ValueError, TypeError, KeyError) as exc:
        raise VaultError('Invalid encrypted profile.') from exc


def _open(parts, secret):
    _, nonce, ciphertext, aad = parts
    if len(secret) != 32:
        raise VaultError('Invalid unlock key.')
    try:
        return json.loads(AESGCM(secret).decrypt(nonce, ciphertext, aad))
    except (InvalidTag, ValueError, UnicodeError):
        raise VaultError('Wrong key or modified encrypted profile.') from None


def unlock_with_password(envelope, password):
    parts = _cipher_parts(envelope)
    secret = key(password, parts[0])
    return _open(parts, secret), secret


def decrypt(envelope, password):
    payload, _ = unlock_with_password(envelope, password)
    return payload


def decrypt_with_key(envelope, secret):
    return _open(_cipher_parts(envelope), secret)


def read_envelope(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise VaultError('Encrypted profile is too large.')
    return json.loads(path.read_text())


def load(path, password):
    return decrypt(read_envelope(path), password)


def save(path,payload,password):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    envelope=encrypt(payload,password)
    fd,tmp=tempfile.mkstemp(prefix='.vault-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w')as f:json.dump(envelope,f,indent=2);f.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
