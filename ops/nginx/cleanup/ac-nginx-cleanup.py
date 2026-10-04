#!/usr/bin/python3
"""Conservative rootful NGINX retention. Default: dry run."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import subprocess

BASE = Path('/var/lib/ac-nginx')
PODMAN = '/usr/local/bin/podman'
PREFIX = 'localhost/nginx-container:'
ROLLBACK = re.compile(r'nginx1-rollback-\d{8}T\d{6}Z-[0-9a-f]{8}')
SHA = re.compile(r'[0-9a-f]{40}')
ENV = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
           HOME=pwd.getpwuid(0).pw_dir, USER='root', LOGNAME='root', LANG='C.UTF-8')

def pod(*args):
    return subprocess.check_output([PODMAN, '--remote=false', *args],
                                   env=ENV, cwd='/', text=True, timeout=120)

def norm(value):
    return value.removeprefix('sha256:')

def containers():
    ids = pod('ps', '-aq', '--no-trunc').split()
    return json.loads(pod('container', 'inspect', *ids)) if ids else []

def plan(cs, records, receipts, images):
    current = [c for c in cs if c['Name'].lstrip('/') == 'nginx1']
    if len(current) != 1 or not current[0]['State']['Running']:
        raise RuntimeError('nginx1 must be running')
    if any(r.get('phase') not in ('healthy', 'rolled_back') for r, _ in records):
        raise RuntimeError('unfinished deployment journal; inspect recovery state first')
    proven = {r['backup_name']: r for r, _ in records if r['phase'] == 'healthy'}
    backups = []
    for c in cs:
        name = c['Name'].lstrip('/')
        r = proven.get(name)
        if (ROLLBACK.fullmatch(name) and r and r['old_id'] == c['Id']
                and norm(r['old_image']) == norm(c['Image'])):
            backups.append(c)
    backups.sort(key=lambda c: c['Name'], reverse=True)
    remove = [c for c in backups[2:] if c['State']['Status'] == 'exited']
    removed_ids = {c['Id'] for c in remove}
    used = {norm(c['Image']) for c in cs if c['Id'] not in removed_ids}
    healthy = {}
    for r, stamp in records:
        if r['phase'] == 'healthy':
            key = (r['revision'], norm(r['new_image']))
            healthy[key] = max(healthy.get(key, 0), stamp)
    eligible = {}
    for receipt, stamp in receipts:
        revision = receipt['revision']
        if not SHA.fullmatch(revision) or receipt['image'] != PREFIX + revision:
            raise RuntimeError('invalid import receipt')
        key = (revision, norm(receipt['image_id']))
        # Reimport after successful deployment is pending again: preserve it.
        if healthy.get(key, 0) > stamp:
            eligible[PREFIX + revision] = key[1]
    image_remove = []
    for image in images:
        image_id = norm(image['Id'])
        tags = image.get('RepoTags') or []
        if (image_id not in used and tags
                and all(eligible.get(tag) == image_id for tag in tags)):
            image_remove.append((image['Id'], tags))
    return remove, image_remove

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('run as root')
    os.umask(0o077)
    with contextlib.ExitStack() as stack:
        # Fixed order; nonblocking. Existing importer/deployer need no changes.
        for path in [BASE / 'deployments/lock', BASE / 'imports/import.lock']:
            lock = stack.enter_context(path.open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        records = [(json.loads(p.read_text()), p.stat().st_mtime_ns)
                   for p in (BASE / 'deployments').glob('*.json')]
        receipts = [(json.loads(p.read_text()), p.stat().st_mtime_ns)
                    for p in (BASE / 'imports').glob('*.json')]
        ids = sorted(set(pod('images', '-aq', '--no-trunc').split()))
        images = json.loads(pod('image', 'inspect', *ids)) if ids else []
        remove, image_remove = plan(containers(), records, receipts, images)
        mode = 'APPLY' if args.apply else 'DRY_RUN'
        for c in remove:
            print(mode, 'REMOVE_CONTAINER', c['Name'], c['Id'], flush=True)
        for image_id, tags in image_remove:
            print(mode, 'REMOVE_IMAGE', image_id, ','.join(tags), flush=True)
        if args.apply:
            for c in remove:
                check = json.loads(pod('container', 'inspect', c['Id']))[0]
                if check['Name'] != c['Name'] or check['State']['Status'] != 'exited':
                    raise RuntimeError('container changed; stopping cleanup')
                pod('rm', c['Id'])
            used_now = {norm(c['Image']) for c in containers()}
            for image_id, tags in image_remove:
                check = json.loads(pod('image', 'inspect', image_id))[0]
                if norm(image_id) in used_now or set(check.get('RepoTags') or []) != set(tags):
                    raise RuntimeError('image references changed; stopping cleanup')
                pod('rmi', '--no-prune', image_id)
        print(mode + '_OK: containers=' + str(len(remove)) + '; images=' + str(len(image_remove)))

if __name__ == '__main__':
    try:
        main()
        if (BASE / 'config-cleanup-enabled').is_file():
            command = ['/usr/local/sbin/ac-nginx-config-cleanup']
            if '--apply' in __import__('sys').argv[1:]: command.append('--apply')
            subprocess.run(command, env=ENV, cwd='/', check=True, timeout=300)
    except BlockingIOError:
        print('CLEANUP_BUSY: import/deploy/cleanup is running; nothing removed')
        raise SystemExit(1)
    except Exception as error:
        print('CLEANUP_FAILED: ' + str(error))
        raise SystemExit(1)
