import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import ac_nginx_release as release

def load():
    loader = importlib.machinery.SourceFileLoader('deploy_test', str(BASE / 'ac-nginx-deploy'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module

class Transactions(unittest.TestCase):
    def scenario(self, mode, fail=False, same=False, corrupt=False):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            d = load()
            d.STATE = root / 'state'; d.STATE.mkdir()
            d.IMPORTS = root / 'imports'; d.IMPORTS.mkdir()
            d.SERVICE = str(root / 'service'); Path(d.SERVICE).mkdir()
            d.RELEASES = root / 'releases'; d.RELEASES.mkdir()
            old_config = str(d.RELEASES / ('a' * 40) / 'nginx')
            new_config = str(d.RELEASES / ('b' * 40) / 'nginx')
            Path(old_config).mkdir(parents=True); Path(new_config).mkdir(parents=True)
            d.MOUNTS = [(old_config, '/etc/nginx', False)]
            image1 = 'sha256:' + '1' * 64; image2 = 'sha256:' + '2' * 64
            revision1 = 'c' * 40; revision2 = 'd' * 40
            for rev, image in ((revision1, image1), (revision2, image2)):
                (d.IMPORTS / (rev + '.json')).write_text(json.dumps({'revision': rev, 'image_id': image}))
            old = {'Id': 'original', 'Name': 'nginx1', 'Image': image1,
                   'State': {'Running': True}, 'HostConfig': {'RestartPolicy': {'Name': 'no'}},
                   'Mounts': [{'Destination': '/etc/nginx', 'Source': old_config, 'RW': False, 'Type': 'bind'}]}
            containers = {'original': old}; events = []; checks = []
            def inspect(name):
                for c in containers.values():
                    if c['Id'] == name or c['Name'] == name:
                        return copy.deepcopy(c)
                raise RuntimeError('missing container')
            def run(args, **kw):
                if args[0] == d.CTL:
                    c = next(c for c in containers.values() if c['Name'] == 'nginx1')
                    c['State']['Running'] = args[1] == 'start'
                    events.append(args[1])
                return types.SimpleNamespace(returncode=0, stdout='')
            def pod(*args, **kw):
                if args[:2] == ('image', 'inspect'):
                    image = args[2]
                    rev = revision1 if image == image1 else revision2
                    return types.SimpleNamespace(stdout=json.dumps([{'Labels': {'org.opencontainers.image.revision': rev},
                        'Config': {'Entrypoint': ['/entrypoint.sh'], 'Cmd': ['nginx', '-g', 'daemon off;']}}]))
                if args[0] == 'rename': containers[args[1]]['Name'] = args[2]
                if args[0] == 'rm' and args[-1] == 'nginx1':
                    cid = next(c['Id'] for c in containers.values() if c['Name'] == 'nginx1')
                    del containers[cid]
                return types.SimpleNamespace(returncode=0, stdout='')
            def create(image):
                c = copy.deepcopy(old); c.update(Id='new', Image=image, Name='nginx1')
                c['Mounts'][0]['Source'] = d.MOUNTS[0][0]
                containers['new'] = c; events.append('create')
            def validate(config):
                if corrupt and config == new_config: raise ValueError('bad hash')
                return Path(config).parent
            def site_checks(config, client):
                checks.append(config)
                if fail and len(checks) == 2: raise RuntimeError('site failed')
            if mode.endswith('-config'):
                arg = 'a' * 40 if same else 'b' * 40
            else: arg = revision1 if same else revision2
            with patch.object(sys, 'argv', ['deploy', mode, arg]), patch.dict(os.environ, {'SUDO_USER':'root'}), \
                 patch.object(d, 'inspect', inspect), patch.object(d, 'run', run), patch.object(d, 'pod', pod), \
                 patch.object(d, 'create', create), patch.object(d, 'healthy', lambda *a: None), \
                 patch.object(d, 'https', lambda: True), patch.object(d, 'check_client', lambda: image2), \
                 patch.object(d, 'validate_config', validate), patch.object(d, 'site_checks', site_checks), \
                 patch.object(d.signal, 'signal'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if fail or corrupt:
                    with self.assertRaises((RuntimeError, ValueError)): d.main()
                else: d.main()
            current = inspect('nginx1')
            records = [json.loads(p.read_text()) for p in d.STATE.glob('*.json')]
            if corrupt or same or mode.startswith('--check'):
                self.assertEqual(current['Id'], 'original'); self.assertNotIn('stop', events)
            elif fail:
                self.assertEqual(current['Id'], 'original')
                self.assertEqual(records[0]['phase'], 'rolled_back')
                self.assertEqual(checks[-1], old_config)
            else:
                self.assertEqual(current['Id'], 'new')
                self.assertEqual(records[0]['phase'], 'healthy')
                self.assertEqual(records[0]['revision'], revision1 if mode.endswith('-config') else revision2)
                self.assertEqual(current['Image'], image1 if mode.endswith('-config') else image2)
                self.assertEqual(current['Mounts'][0]['Source'], new_config if mode.endswith('-config') else old_config)
    def test_image_keeps_config(self): self.scenario('--deploy')
    def test_config_keeps_image(self): self.scenario('--deploy-config')
    def test_failed_site_rolls_back(self): self.scenario('--deploy-config', fail=True)
    def test_failed_image_sites_roll_back(self): self.scenario('--deploy', fail=True)
    def test_noop_config(self): self.scenario('--deploy-config', same=True)
    def test_noop_image(self): self.scenario('--deploy', same=True)
    def test_check_does_not_stop(self): self.scenario('--check-config')
    def test_corrupt_before_stop(self): self.scenario('--deploy-config', corrupt=True)

class Integrity(unittest.TestCase):
    def test_manifest(self):
        with tempfile.TemporaryDirectory() as work:
            releases = Path(work) / 'releases'; releases.mkdir()
            root = releases / ('e' * 40)
            (root / 'nginx').mkdir(parents=True)
            (root / 'source/tools').mkdir(parents=True)
            (root / 'nginx/nginx.conf').write_text('events {}')
            (root / 'source/tools/config.py').write_text('# source')
            hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in root.rglob('*') if p.is_file()}
            (root / 'release.json').write_text(json.dumps({'revision': root.name, 'sha256': hashes}))
            # Ignore /tmp permissions in fixture, retain actual file type checks.
            real_trusted = release.trusted
            def trusted(p, directory=False):
                if p == Path(work) or Path(work) in p.parents: real_trusted(p, directory)
            with patch.object(release, 'RELEASES', releases), patch.object(release, 'trusted', trusted):
                self.assertEqual(release.validate_config(str(root/'nginx')), root)
                (root/'nginx/extra').write_text('unexpected')
                with self.assertRaises(ValueError): release.validate_config(str(root/'nginx'))
                (root/'nginx/extra').unlink()
                (root/'nginx/nginx.conf').write_text('changed')
                with self.assertRaises(ValueError): release.validate_config(str(root/'nginx'))
                (root/'nginx/nginx.conf').unlink()
                (root/'nginx/nginx.conf').symlink_to('/etc/passwd')
                with self.assertRaises(ValueError): release.validate_config(str(root/'nginx'))

if __name__ == '__main__': unittest.main()

class CleanupCompatibility(unittest.TestCase):
    def test_config_journal_retains_image_revision(self):
        loader = importlib.machinery.SourceFileLoader('cleanup_test', str(BASE.parent / 'cleanup/ac-nginx-cleanup.py'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cleanup = importlib.util.module_from_spec(spec); loader.exec_module(cleanup)
        image = 'sha256:' + '1' * 64
        revision = 'c' * 40
        record = {'revision': revision, 'config_revision': 'b' * 40, 'operation': 'config',
                  'phase': 'healthy', 'new_image': image, 'old_image': image, 'old_id': 'old',
                  'backup_name': 'nginx1-rollback-20261004T150000Z-12345678'}
        current = {'Id': 'new', 'Name': 'nginx1', 'Image': image, 'State': {'Running': True}}
        receipt = {'revision': revision, 'image_id': image, 'image': cleanup.PREFIX + revision}
        self.assertEqual(cleanup.plan([current], [(record, 2)], [(receipt, 1)],
                         [{'Id': image, 'RepoTags': [receipt['image']]}]), ([], []))
