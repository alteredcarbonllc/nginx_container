#!/usr/bin/python3
"""Conservative configuration retention; dry-run unless --apply. No image/container deletion."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import uuid
sys.path.insert(0, '/usr/local/libexec')
from ac_nginx_release import RELEASES, validate_config, trusted

BASE = Path('/var/lib/ac-nginx')
INBOX = Path('/var/spool/ac-nginx/config-inbox')
QUARANTINE = Path('/var/spool/ac-nginx/config-cleanup-quarantine')
PODMAN = '/usr/local/bin/podman'
SHA = re.compile('[0-9a-f]{40}')
GRACE_NS = 24 * 60 * 60 * 10**9
MAX_BYTES = 20 * 1024 * 1024
ENV = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
           HOME=pwd.getpwuid(0).pw_dir, USER='root', LOGNAME='root', LANG='C.UTF-8')

def containers():
    def pod(*args):
        return subprocess.check_output([PODMAN, '--remote=false', *args], env=ENV, cwd='/', text=True, timeout=120)
    ids = pod('ps', '-aq', '--no-trunc').split()
    return json.loads(pod('container', 'inspect', *ids)) if ids else []

def overlaps(source, release):
    source = Path(source).resolve()
    release = Path(release).resolve()
    return source == release or source in release.parents or release in source.parents

def signature(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

def archive_info(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MAX_BYTES:
            raise ValueError('not a small regular archive')
        with tarfile.open(fileobj=f, mode='r:') as tar:
            seen = set(); hashes = {}; size = 0
            for index, m in enumerate(tar):
                name = str(Path(m.name))
                size += m.size
                if index >= 10000 or size > MAX_BYTES or m.size < 0:
                    raise ValueError('archive limits exceeded')
                if Path(name).is_absolute() or '..' in Path(name).parts or name == '.' or name in seen:
                    raise ValueError('unsafe or duplicate archive path')
                seen.add(name)
                if m.isdir(): continue
                if not m.isfile(): raise ValueError('archive link/special file')
                with tar.extractfile(m) as src:
                    hashes[name] = hashlib.sha256(src.read()).hexdigest()
        if hashes != expected: raise ValueError('archive differs from imported source')
        if signature(os.fstat(f.fileno())) != signature(st): raise ValueError('archive changed during read')
        return signature(st)

def successful(records):
    if any(r.get('phase') not in ('healthy', 'rolled_back') for r, _ in records):
        raise ValueError('unfinished deployment journal; nothing removed')
    result = {}
    for r, stamp in records:
        if r.get('phase') != 'healthy': continue
        rev = r.get('config_revision')
        if isinstance(rev, str) and SHA.fullmatch(rev) and r.get('new_config') == str(RELEASES / rev / 'nginx'):
            result[rev] = max(result.get(rev, 0), stamp)
    return result

def release_candidates(releases, success, cs, pending, now):
    current = [c for c in cs if c['Name'].lstrip('/') == 'nginx1']
    if len(current) != 1 or not current[0]['State']['Running']:
        raise ValueError('nginx1 must be running')
    keep = set(sorted(success, key=success.get, reverse=True)[:2])
    result = []
    for rev, created in releases.items():
        path = RELEASES / rev
        used = any(overlaps(m['Source'], path) for c in cs for m in c.get('Mounts', []) if m.get('Type') == 'bind')
        if (not used and rev not in keep and rev not in pending
                and success.get(rev, 0) > created and now - created >= GRACE_NS):
            result.append(rev)
    return sorted(result)

def restore_archive(moved, original):
    # Never overwrite a concurrent new upload. Hardlink is atomic and fails if name exists.
    try:
        os.link(moved, original, follow_symlinks=False)
        moved.unlink()
        print('ARCHIVE_RESTORED:', original)
    except FileExistsError:
        print('ARCHIVE_PRESERVED_IN_QUARANTINE:', moved)

def remove_archive(path, expected, sig):
    # Rename first; a concurrent replacement is detected after taking possession.
    moved = QUARANTINE / (path.name + '.' + uuid.uuid4().hex)
    os.rename(path, moved)
    try:
        if archive_info(moved, expected) != sig:
            raise ValueError('concurrent archive replacement')
    except Exception:
        restore_archive(moved, path)
        raise
    moved.unlink()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0: raise ValueError('run as root')
    os.umask(0o077)
    with (BASE / 'deployments/lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for parent in reversed(INBOX.parents): trusted(parent, True)
        st = INBOX.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != pwd.getpwnam('ac-ci-builder').pw_uid or stat.S_IMODE(st.st_mode) != 0o700:
            raise ValueError('unexpected inbox ownership/mode')
        trusted(RELEASES, True)
        records = []
        for p in (BASE / 'deployments').glob('*.json'):
            trusted(p)
            records.append((json.loads(p.read_text()), p.stat().st_mtime_ns))
        success = successful(records)
        cs = containers()
        now = time.time_ns()
        releases = {}; manifests = {}; pending = set(); archive_remove = []
        for p in sorted(RELEASES.iterdir()):
            if not SHA.fullmatch(p.name): continue  # migration-v1 and staging are always retained
            validate_config(str(p / 'nginx'))
            manifest = p / 'release.json'
            releases[p.name] = max(p.stat().st_mtime_ns, manifest.stat().st_mtime_ns)
            manifests[p.name] = json.loads(manifest.read_text())
        for path in sorted(INBOX.glob('*.tar')):
            rev = path.stem
            if not SHA.fullmatch(rev): continue
            pending.add(rev)
            if rev not in manifests: continue
            expected = {k.removeprefix('source/'): v for k, v in manifests[rev]['sha256'].items() if k.startswith('source/')}
            try: sig = archive_info(path, expected)
            except (OSError, ValueError, tarfile.TarError) as error:
                print('KEEP_ARCHIVE:', path.name, str(error)); continue
            if success.get(rev, 0) > sig[3]:
                pending.discard(rev)
                if now - sig[3] >= GRACE_NS:
                    archive_remove.append((path, expected, sig))
                else:
                    pending.add(rev)  # retain release until its archive can also be collected
        remove = release_candidates(releases, success, cs, pending, now)
        mode = 'APPLY' if args.apply else 'DRY_RUN'
        for path, _, _ in archive_remove: print(mode, 'REMOVE_CONFIG_ARCHIVE', path)
        for rev in remove: print(mode, 'REMOVE_CONFIG_RELEASE', RELEASES / rev)
        if args.apply:
            for parent in reversed(QUARANTINE.parents): trusted(parent, True)
            if not QUARANTINE.exists(): QUARANTINE.mkdir(mode=0o700)
            trusted(QUARANTINE, True)
            for path, expected, sig in archive_remove: remove_archive(path, expected, sig)
            for rev in remove:
                # Recheck all containers, including non-nginx and stopped containers.
                fresh = containers()
                if any(overlaps(m['Source'], RELEASES / rev) for c in fresh for m in c.get('Mounts', []) if m.get('Type') == 'bind'):
                    raise ValueError('release acquired a container reference; stopped cleanup')
                if (INBOX / (rev + '.tar')).exists() or (INBOX / (rev + '.tar')).is_symlink():
                    raise ValueError('release acquired an inbox upload; stopped cleanup')
                validate_config(str(RELEASES / rev / 'nginx'))
                shutil.rmtree(RELEASES / rev)
        print(mode + '_CONFIG_OK: releases=' + str(len(remove)) + '; archives=' + str(len(archive_remove)))

if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('CONFIG_CLEANUP_FAILED: ' + str(error), file=sys.stderr)
        sys.exit(1)
