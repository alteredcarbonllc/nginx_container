import importlib.machinery
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE.parent/'deploy'))
loader=importlib.machinery.SourceFileLoader('config_cleanup',str(BASE/'ac-nginx-config-cleanup.py'))
spec=importlib.util.spec_from_loader(loader.name,loader)
c=importlib.util.module_from_spec(spec);loader.exec_module(c)

class Retention(unittest.TestCase):
    def setUp(self):
        self.revs=[str(i)*40 for i in range(1,6)]
        self.now=10*c.GRACE_NS
        self.success={r:self.now-2*c.GRACE_NS+i for i,r in enumerate(self.revs)}
        self.releases={r:self.now-3*c.GRACE_NS for r in self.revs}
        self.cs=[dict(Name='nginx1',State={'Running':True},Mounts=[])]
    def plan(self,pending=set()):
        return c.release_candidates(self.releases,self.success,self.cs,pending,self.now)
    def test_keep_two_latest(self): self.assertEqual(self.plan(),self.revs[:3])
    def test_keep_pending(self): self.assertNotIn(self.revs[0],self.plan({self.revs[0]}))
    def test_keep_unproven(self):
        del self.success[self.revs[0]];self.assertNotIn(self.revs[0],self.plan())
    def test_keep_new_release(self):
        self.releases[self.revs[0]]=self.now;self.assertNotIn(self.revs[0],self.plan())
    def test_keep_stopped_other_container(self):
        self.cs.append(dict(Name='unrelated',State={'Running':False},Mounts=[dict(Type='bind',Source=str(c.RELEASES/self.revs[0]/'source'))]))
        self.assertNotIn(self.revs[0],self.plan())
    def test_parent_mount(self):
        self.cs[0]['Mounts']=[dict(Type='bind',Source=str(c.RELEASES))]
        self.assertEqual(self.plan(),[])
    def test_unfinished(self):
        with self.assertRaises(ValueError):c.successful([({'phase':'created'},1)])
    def test_rollback_is_not_proof(self):
        self.assertEqual(c.successful([({'phase':'rolled_back','config_revision':self.revs[0]},1)]),{})
    def test_require_running(self):
        self.cs[0]['State']['Running']=False
        with self.assertRaises(ValueError):self.plan()

class Archives(unittest.TestCase):
    def make(self,path,text):
        raw=text.encode()
        with tarfile.open(path,'w') as t:
            m=tarfile.TarInfo('nginx/nginx.conf');m.size=len(raw);t.addfile(m,io.BytesIO(raw))
        return {'nginx/nginx.conf':hashlib.sha256(raw).hexdigest()}
    def test_match_delete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'archive.tar';q=root/'q';q.mkdir()
            expected=self.make(p,'same');sig=c.archive_info(p,expected)
            with patch.object(c,'QUARANTINE',q):c.remove_archive(p,expected,sig)
            self.assertFalse(p.exists());self.assertEqual(list(q.iterdir()),[])
    def test_replacement_restored(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'archive.tar';q=root/'q';q.mkdir()
            expected=self.make(p,'same');sig=c.archive_info(p,expected)
            newer=root/'new';self.make(newer,'different');newer.replace(p)
            with patch.object(c,'QUARANTINE',q):
                with self.assertRaises(ValueError):c.remove_archive(p,expected,sig)
            self.assertTrue(p.exists());self.assertEqual(list(q.iterdir()),[])
    def test_restore_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=root/'moved';b=root/'incoming';a.write_text('old');b.write_text('new')
            c.restore_archive(a,b)
            self.assertEqual(b.read_text(),'new');self.assertEqual(a.read_text(),'old')
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'a';p.symlink_to('/etc/passwd')
            with self.assertRaises(OSError):c.archive_info(p,{})
    def test_different_source_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a';self.make(p,'x')
            with self.assertRaises(ValueError):c.archive_info(p,{'different':'hash'})
