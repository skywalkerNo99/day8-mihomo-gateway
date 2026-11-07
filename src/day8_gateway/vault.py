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

def decrypt(envelope,password):
    try:
        if set(envelope)!={'format','version','kdf','cipher','salt','nonce','ciphertext'}:
            raise VaultError('Unsupported vault fields.')
        if envelope['format']!=FORMAT or envelope['version']!=1 or envelope['kdf']!=PARAMS or envelope['cipher']!='aes-256-gcm':
            raise VaultError('Unsupported vault format or KDF parameters.')
        salt=decode(envelope['salt']);nonce=decode(envelope['nonce']);ciphertext=decode(envelope['ciphertext'])
        if len(salt)!=16 or len(nonce)!=12 or not 16<=len(ciphertext)<=MAX_BYTES:
            raise VaultError('Invalid vault lengths.')
        header={k:v for k,v in envelope.items()if k!='ciphertext'}
        plaintext=AESGCM(key(password,salt)).decrypt(nonce,ciphertext,canonical(header))
        return json.loads(plaintext)
    except InvalidTag:
        raise VaultError('Wrong password or modified encrypted profile.')from None
    except VaultError:raise
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise VaultError('Invalid encrypted profile.')from None

def load(path,password):
    path=Path(path)
    if path.stat().st_size>MAX_BYTES:raise VaultError('Encrypted profile is too large.')
    return decrypt(json.loads(path.read_text()),password)

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
