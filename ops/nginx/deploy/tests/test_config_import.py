import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

class ImportTests(unittest.TestCase):
    def scenario(self, kind):
        loader = importlib.machinery.SourceFileLoader('prepare_test', str(BASE / 'ac-nginx-config-prepare'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        p = importlib.util.module_from_spec(spec); loader.exec_module(p)
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            inbox = root / 'inbox'; inbox.mkdir()
            releases = root / 'releases'; releases.mkdir()
            revision = 'a' * 40
            archive = inbox / (revision + '.tar')
            with tarfile.open(archive, 'w') as tar:
                for name, text in [('tools/config.py', '# not executed'), ('nginx/nginx.conf', 'events {}')]:
                    m = tarfile.TarInfo(name); raw = text.encode(); m.size = len(raw)
                    tar.addfile(m, io.BytesIO(raw))
                if kind in ('traversal', 'link'):
                    m = tarfile.TarInfo('../escape' if kind == 'traversal' else 'link')
                    if kind == 'link': m.type = tarfile.SYMTYPE; m.linkname = '/etc/passwd'
                    tar.addfile(m)
            if kind == 'inbox_symlink':
                original = inbox / 'other'; archive.rename(original); archive.symlink_to(original)
            if kind in ('same', 'collision'):
                previous = releases / revision / 'source'
                (previous / 'tools').mkdir(parents=True)
                (previous / 'nginx').mkdir()
                (previous / 'tools/config.py').write_text('# not executed')
                (previous / 'nginx/nginx.conf').write_text('changed' if kind == 'collision' else 'events {}')
            def run(args, **kw):
                if 'build' in args:
                    output = Path(args[args.index('--output') + 1]); output.mkdir()
                    (output / 'nginx.conf').write_text('events {}')
                return types.SimpleNamespace(stdout='sha256:' + '1'*64)
            actual_open = open
            def redirect_open(name, *args, **kw):
                if str(name) == '/var/lib/ac-nginx/deployments/lock': name = root / 'lock'
                return actual_open(name, *args, **kw)
            with patch.object(p, 'RELEASES', releases), patch.object(p, 'INBOX', inbox), \
                 patch.object(p, 'trusted'), patch.object(p, 'validate_config'), patch.object(p, 'run', run), \
                 patch.object(p.subprocess, 'run'), patch('builtins.open', redirect_open), \
                 patch.object(sys, 'argv', ['prepare', '--import', revision]), contextlib.redirect_stdout(io.StringIO()):
                if kind in ('traversal','link','inbox_symlink','collision'):
                    with self.assertRaises((ValueError, OSError)): p.main()
                else:
                    p.main()
                    if kind == 'valid':
                        manifest = json.loads((releases/revision/'release.json').read_text())
                        self.assertEqual(manifest['revision'], revision)
                        self.assertIn('source/tools/config.py', manifest['sha256'])
            self.assertFalse((root/'escape').exists())
            # Allow ordinary non-root developer accounts to remove frozen fixtures.
            for item in releases.rglob('*'):
                if item.is_dir(): item.chmod(0o700)
    def test_valid_import(self): self.scenario('valid')
    def test_repeat_same_source(self): self.scenario('same')
    def test_collision(self): self.scenario('collision')
    def test_traversal(self): self.scenario('traversal')
    def test_symlink(self): self.scenario('link')
    def test_inbox_symlink(self): self.scenario('inbox_symlink')
