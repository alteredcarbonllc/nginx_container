import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import fcntl

SCRIPT = Path(__file__).resolve().parents[1] / 'ac-ci-builder-store'
loader = importlib.machinery.SourceFileLoader('store', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def row(n, age=20, repo='localhost/nginx-container'):
    return {'Id': f'{n:064x}', 'RepoTags': [f'{repo}:{n:040x}'],
            'Created': (NOW - timedelta(days=age)).isoformat(), 'Parent': ''}


class Retention(unittest.TestCase):
    def setUp(self):
        self.rows = [row(n, age=20-n) for n in range(1, 6)]

    def ids(self, rows=None, used=()):
        return [i for i, tags in m.candidates(self.rows if rows is None else rows, set(used), NOW)]

    def test_keep_three(self):
        self.assertEqual(self.ids(), [f'{1:064x}', f'{2:064x}'])

    def test_keep_container_image(self):
        self.assertEqual(self.ids(used=[f'{1:064x}']), [f'{2:064x}'])

    def test_young_images(self):
        self.assertEqual(self.ids([row(n, age=6) for n in range(1, 9)]), [])

    def test_exact_seven_days(self):
        self.assertEqual(self.ids([row(n, age=7) for n in range(1, 9)]), [])

    def test_independent_repos(self):
        rows = self.rows + [row(n, repo='localhost/php-carbonblog') for n in range(6, 11)]
        self.assertEqual(set(self.ids(rows)), {f'{n:064x}' for n in (1, 2, 6, 7)})

    def test_redis_retention(self):
        self.assertEqual(m.REPOS['redis'], 'localhost/redis-ac')
        rows = [row(n, age=20-n, repo='localhost/redis-ac') for n in range(1, 6)]
        self.assertEqual(self.ids(rows), [f'{1:064x}', f'{2:064x}'])
        self.assertEqual(self.ids(rows, used=[f'{1:064x}']), [f'{2:064x}'])

    def test_postgresql_retention(self):
        rows=[row(n, age=20-n, repo='localhost/postgresql-ac') for n in range(1,6)]
        self.assertEqual(self.ids(rows), [f'{1:064x}',f'{2:064x}'])
        self.assertEqual(m.REPOS['postgresql'],'localhost/postgresql-ac')

    def test_mail_and_fixture_retention(self):
        for key,repo in [('dovecot','localhost/dovecot-ac'),('postfix','localhost/postfix-ac'),
                         ('dovecot-fixture','localhost/dovecot-ci-fixture'),
                         ('postfix-fixture','localhost/postfix-ci-fixture')]:
            self.assertEqual(m.REPOS[key],repo)
            rows=[row(n,age=20-n,repo=repo) for n in range(1,6)]
            self.assertEqual(self.ids(rows),[f'{1:064x}',f'{2:064x}'])

    def test_base_and_untagged(self):
        extra = [row(20, repo='docker.io/library/php'), row(21)]
        extra[1]['RepoTags'] = None
        self.assertEqual(self.ids(self.rows + extra), self.ids())

    def test_unknown_extra_tag_protects(self):
        self.rows[0]['RepoTags'].append('localhost/keep:latest')
        self.assertNotIn(f'{1:064x}', self.ids())

    def test_parent_protected(self):
        self.rows[-1]['Parent'] = 'sha256:' + f'{1:064x}'
        self.assertNotIn(f'{1:064x}', self.ids())

    def test_duplicate_id_refused(self):
        with self.assertRaises(ValueError):
            self.ids(self.rows + [self.rows[0]])

    def test_bad_time_refused(self):
        self.rows[0]['Created'] = 'not-a-date'
        with self.assertRaises(ValueError):
            self.ids()

    def test_future_preserved(self):
        self.assertEqual(self.ids([row(n, age=-1) for n in range(1, 9)]), [])

    def test_nanosecond_timestamp(self):
        self.assertEqual(m.created('2026-10-01T00:00:00.123456789Z').microsecond, 123456)

    def test_no_versions(self):
        self.assertEqual(self.ids([]), [])


class Locks(unittest.TestCase):
    def test_shared_builds_exclude_cleanup_and_survive_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            old_state, old_uid = m.STATE, m.UID
            m.STATE, m.UID = Path(directory), os.getuid()
            try:
                path = m.STATE / 'store.lock'
                path.touch(mode=0o600)
                fd = m.lock('store.lock', fcntl.LOCK_SH)
                try:
                    code = '''import fcntl,sys
f=open(sys.argv[1], 'r+')
try:
 fcntl.flock(f, int(sys.argv[2]) | fcntl.LOCK_NB)
except BlockingIOError:
 sys.exit(42)
'''
                    for operation, expected in [(fcntl.LOCK_SH, 0), (fcntl.LOCK_EX, 42)]:
                        result = subprocess.run([sys.executable, '-c', code, str(path), str(operation)])
                        self.assertEqual(result.returncode, expected)
                    # A child exec inherits the lock while parent releases its descriptor.
                    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                             stdin=subprocess.PIPE, pass_fds=(fd,))
                    os.close(fd)
                    fd = None
                    try:
                        result = subprocess.run([sys.executable, '-c', code, str(path), str(fcntl.LOCK_EX)])
                        self.assertEqual(result.returncode, 42)
                    finally:
                        child.communicate(b'', timeout=5)
                    self.assertEqual(subprocess.run([sys.executable, '-c', code, str(path), str(fcntl.LOCK_EX)]).returncode, 0)
                finally:
                    if fd is not None:
                        os.close(fd)
            finally:
                m.STATE, m.UID = old_state, old_uid


if __name__ == '__main__':
    unittest.main()
