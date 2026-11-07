#!/usr/bin/env python3
"""Pre-commit hygiene check; never prints suspicious file contents."""
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {'.git', '.venv', '.tools', '.local', '__pycache__'}


def check():
    errors = []
    for path in ROOT.rglob('*'):
        if not path.is_file() or any(p in EXCLUDE for p in path.relative_to(ROOT).parts):
            continue
        rel = path.relative_to(ROOT)
        if path.suffix in ('.pyc', '.log', '.bak', '.gz', '.tar') or '.private.' in path.name or path.name.startswith('.env'):
            errors.append(f'Forbidden artifact: {rel}')
        try:
            text = path.read_text()
        except (UnicodeError, OSError):
            errors.append(f'Unexpected binary/unreadable file: {rel}')
            continue
        # Catch accidental machine-specific paths without printing their contents.
        if ('/' + 'Users/') in text or ('/' + 'home/') in text:
            errors.append(f'Machine-specific path: {rel}')
        if rel.parts[0] == 'secrets' and path.name != 'README.md':
            try:
                env = json.loads(text)
                assert path.name.endswith('.enc.json')
                assert set(env) == {'format', 'version', 'kdf', 'cipher', 'salt', 'nonce', 'ciphertext'}
                assert env['format'] == 'day8-mihomo-vault' and env['version'] == 1
                assert env['cipher'] == 'aes-256-gcm'
                assert env['kdf'] == {'name': 'argon2id', 'memory_kib': 65536, 'iterations': 3, 'lanes': 4}
                assert len(base64.b64decode(env['salt'], validate=True)) == 16
                assert len(base64.b64decode(env['nonce'], validate=True)) == 12
                assert len(base64.b64decode(env['ciphertext'], validate=True)) >= 16
            except (AssertionError, ValueError, TypeError, KeyError):
                errors.append(f'Only authenticated vault envelopes are allowed in secrets: {rel}')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Repository hygiene passed (encrypted envelopes only; no runtime artifacts).')
    return 0


if __name__ == '__main__':
    sys.exit(check())
