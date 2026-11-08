"""Authenticated Day8-only updates tied to the immutable enrolled base vault."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from . import config, vault

LIMIT = 16384
FIELDS = {'server', 'port', 'cipher', 'server_password', 'user_password', 'max_padding'}


def validate_node(base, node):
    if not isinstance(node, dict) or set(node) - FIELDS or not 0 < len(vault.canonical(node)) < 5000:
        raise ValueError('Invalid Day8 node.')
    config.validate({**base, 'day8': node})
    return node


def aad(profile):
    return b'day8-node-v1:' + hashlib.sha256(Path(profile).read_bytes()).digest()


def seal(profile, key, base, node):
    raw = vault.canonical(validate_node(base, node))
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, raw, aad(profile))).decode('ascii')


def unseal(profile, key, base, data):
    if not isinstance(data, str) or len(data) > LIMIT:
        raise ValueError('Invalid update length.')
    raw = base64.b64decode(data, validate=True)
    node = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], aad(profile)))
    return validate_node(base, node)


def save(path, packet):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(packet.encode('ascii'))
            f.flush()
            os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def load_saved(root, profile, key, payload):
    path = Path(root) / '.local/node.enc'
    if path.exists():
        payload['day8'] = unseal(profile, key, payload, path.read_text('ascii'))
