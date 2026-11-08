"""Read an explicit Muon outbound if the official desktop configuration provides one."""
import tomllib
from pathlib import Path

from .node_updates import FIELDS


def selected(document):
    router = document.get('router', {})
    strategy = next((s for s in router.get('strategies', [])
                     if s.get('label') == router.get('active_strategy')), {})
    target = strategy.get('default_outbound')
    options = []
    for item in document.get('outbounds', []):
        if not isinstance(item, dict) or item.get('protocol') != 'muon':
            continue
        if target and item.get('label') != target:
            continue
        data = item.get('settings', item)
        node = {k: data[k] for k in FIELDS if k in data}
        if {'server', 'port', 'cipher', 'server_password', 'user_password'} <= node.keys():
            options.append(node)
    return options[0] if len(options) == 1 else None


def desktop_node():
    path = Path.home() / 'Library/Application Support/Day8/config.toml'
    if not path.is_file():
        return None
    with path.open('rb') as f:
        return selected(tomllib.load(f))
