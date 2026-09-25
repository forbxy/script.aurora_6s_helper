import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
from backup_identity import read_identity, sha, RECORDS, BINDING, EVIDENCE
from policy import backup_matches

class LegacyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.saved=dict(emmc_bytes=62537072640,android_models=['A4111'],boot_areas={'mmcblk0boot0':4194304,'mmcblk0boot1':4194304},mpt_sha256='e'*64)
        for name in RECORDS:(self.root/name).write_text(json.dumps(self.saved))
        ticket=dict(kind='metadata-only-reset-layout-trial',cid='a'*32,backup='/storage/old',emmc_bytes=self.saved['emmc_bytes'],hashes={'original':{'mpt':self.saved['mpt_sha256']}})
        (self.root/EVIDENCE).write_text(json.dumps(ticket))
        self.binding=dict(schema=1,kind='audited-legacy-layout-ticket',cid='a'*32,original_backup_name='old',record_sha256={n:sha(self.root/n) for n in RECORDS},evidence_sha256=sha(self.root/EVIDENCE),**{k:self.saved[k] for k in ('emmc_bytes','android_models','boot_areas')})
        self.save_binding()
    def save_binding(self):
        (self.root/BINDING).write_text(json.dumps(self.binding))
    def test_valid_binding_preserves_original_and_matches_only_same_device(self):
        before={n:(self.root/n).read_bytes() for n in RECORDS}
        identity=read_identity(self.root);self.assertEqual(identity['cid'],'a'*32)
        backup_matches(dict(self.saved,cid='a'*32),identity)
        with self.assertRaises(ValueError):backup_matches(dict(self.saved,cid='b'*32),identity)
        self.assertEqual(before,{n:(self.root/n).read_bytes() for n in RECORDS})
    def test_missing_binding_does_not_invent_cid(self):
        (self.root/BINDING).unlink();self.assertNotIn('cid',read_identity(self.root))
    def test_modified_records_or_evidence_refused(self):
        for name in (*RECORDS,EVIDENCE):
            with self.subTest(name=name):
                p=self.root/name;old=p.read_bytes();p.write_bytes(old+b' ')
                with self.assertRaises(ValueError):read_identity(self.root)
                p.write_bytes(old)
    def test_conflicting_binding_fields_refused(self):
        original=copy.deepcopy(self.binding)
        for key,value in [('cid','b'*32),('android_models',['A4112']),('emmc_bytes',1),('record_sha256',{}),('original_backup_name','unrelated')]:
            with self.subTest(key=key):
                self.binding=copy.deepcopy(original);self.binding[key]=value;self.save_binding()
                with self.assertRaises(ValueError):read_identity(self.root)
    def test_symlink_evidence_refused(self):
        p=self.root/EVIDENCE;p.rename(self.root/'original');p.symlink_to('original')
        with self.assertRaises(ValueError):read_identity(self.root)
    def test_modern_backup_uses_acquisition_cid(self):
        p=self.root/'probe-before.json';p.write_text(json.dumps(dict(self.saved,cid='c'*32)))
        self.assertEqual(read_identity(self.root)['cid'],'c'*32)
