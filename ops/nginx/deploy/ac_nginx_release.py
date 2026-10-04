"""Validation of administrator-prepared NGINX releases; no release code execution."""
import hashlib
import json
from pathlib import Path
import re
import stat

RELEASES = Path('/var/lib/ac-nginx/config-releases')
MIGRATION = RELEASES / 'migration-v1'

def trusted(path, directory=False):
    st = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
        raise ValueError('untrusted release path: ' + str(path))

def config_mount(container):
    found = [m for m in container['Mounts'] if m['Destination'] == '/etc/nginx']
    if len(found) != 1 or found[0]['Type'] != 'bind' or found[0]['RW']:
        raise ValueError('expected one read-only config bind mount')
    return found[0]['Source']

def validate_config(value):
    path = Path(value)
    if str(path) != value:
        raise ValueError('noncanonical config path')
    for parent in reversed(RELEASES.parents):
        trusted(parent, True)
    trusted(RELEASES, True)
    if path == MIGRATION:
        trusted(path, True)
        trusted(path / 'nginx.conf')
        return None
    if path.name != 'nginx' or path.parent.parent != RELEASES or not re.fullmatch('[0-9a-f]{40}', path.parent.name):
        raise ValueError('config must be migration-v1 or config-releases/FULL_SHA/nginx')
    root = path.parent
    trusted(root, True)
    trusted(root / 'release.json')
    record = json.loads((root / 'release.json').read_text())
    if record.get('revision') != root.name or not isinstance(record.get('sha256'), dict):
        raise ValueError('invalid release manifest')
    actual = {}
    for folder in ('source', 'nginx'):
        trusted(root / folder, True)
        for item in sorted((root / folder).rglob('*')):
            if item.is_dir() and not item.is_symlink():
                trusted(item, True)
            else:
                trusted(item)
                actual[str(item.relative_to(root))] = hashlib.sha256(item.read_bytes()).hexdigest()
    if not actual or actual != record['sha256']:
        raise ValueError('release file set or hash mismatch')
    for required in ('nginx/nginx.conf', 'source/tools/config.py'):
        if required not in actual:
            raise ValueError('missing required release file: ' + required)
    return root
