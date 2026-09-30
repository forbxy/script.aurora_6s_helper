"""Journal recovery guards and failure handling; real tools use only temp files."""
import contextlib,json,shutil,struct,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import ce_journal as j


def superblock(dirty=True):
    b=bytearray(1024)
    b[56:58]=b'\x53\xef';b[120:136]=b'CE_AURORA_DATA'.ljust(16,b'\0')
    for off,value in ((4,1024**3//4096),(24,2),(92,4),(96,4 if dirty else 0),(224,8)):
        struct.pack_into('<I',b,off,value)
    return bytes(b)


class JournalTests(unittest.TestCase):
    def test_header_identity_and_journal_guards(self):
        row=dict(name='ce_storage',offset=4096,size=1024**3)
        self.assertTrue(j.inspect_super(superblock(),row))
        self.assertFalse(j.inspect_super(superblock(False),row))
        for off,value in ((4,1),(24,0),(92,0),(96,12),(224,0),(228,1),(208,1),(58,2)):
            b=bytearray(superblock());struct.pack_into('<I',b,off,value)
            with self.assertRaises(ValueError):j.inspect_super(bytes(b),row)
        with self.assertRaises(ValueError):j.inspect_super(superblock(),dict(row,name='userdata'))

    def scenario(self, stack, folder, *, dirty=True, replay_code=0, after_code=0):
        row=dict(name='ce_storage',offset=4096,size=1024**3)
        r={'cid':'test','emmc_bytes':16*1024**3,'mpt':{'partitions':[{'offset':0}]*29},
           'ota_repair':{'partitions':[{},row,{}]}}
        before=superblock(dirty)
        stack.enter_context(patch.object(j.layout,'read_at',side_effect=[before,before,superblock(False)]))
        for name in ('identity','bounded_region'):stack.enter_context(patch.object(j.policy,name))
        for name in ('same_device','external','target_idle'):stack.enter_context(patch.object(j.op,name))
        stack.enter_context(patch.object(j.op,'probe',return_value=r))
        stack.enter_context(patch.object(j,'private_external',side_effect=lambda p:p))
        stack.enter_context(patch.object(j.os,'sync'))
        stack.enter_context(patch.object(j.os,'statvfs',return_value=type('Space',(),{'f_bavail':1024**3,'f_frsize':4})()))
        stack.enter_context(patch.object(j.shutil,'which',return_value='/usr/sbin/e2fsck'))
        root=folder/'data';root.mkdir();(root/'.kodi').mkdir();(root/'.config').mkdir()
        stack.enter_context(patch.object(j.layout,'mounted',return_value=contextlib.nullcontext((root,'unused'))))
        modes=[]
        def mapping(row,r,readonly=False):
            modes.append(readonly);return contextlib.nullcontext(folder/'bounded-device')
        stack.enter_context(patch.object(j.op,'region_loop',side_effect=mapping))
        stack.enter_context(patch.object(j.layout,'require_readonly'))
        calls=[]
        def fsck(args,update,name,**kwargs):
            calls.append(args)
            if '-z' in args:
                Path(args[args.index('-z')+1]).write_bytes(b'undo')
                return subprocess.CompletedProcess(args,replay_code,b'journal',b'')
            return subprocess.CompletedProcess(args,4 if len(calls)==1 else after_code,b'check',b'')
        stack.enter_context(patch.object(j.layout,'run_fsck',side_effect=fsck))
        return r,modes,calls

    def test_clean_never_opens_writable_device(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
            root=Path(tmp);r,modes,calls=self.scenario(st,root,dirty=False)
            self.assertFalse(j.replay_if_needed(root,r,r['ota_repair']['partitions'],lambda *a,**kw:None))
            self.assertEqual(modes,[]);self.assertEqual(calls,[])

    def test_dirty_replays_only_journal_and_rechecks_readonly(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
            root=Path(tmp);r,modes,calls=self.scenario(st,root);events=[]
            self.assertTrue(j.replay_if_needed(root,r,r['ota_repair']['partitions'],lambda *a,**kw:events.append(kw)))
            self.assertEqual(modes,[True,False,True])
            self.assertEqual(calls[1][:5],['e2fsck','-p','-E','journal_only','-z'])
            self.assertEqual(calls[2][:3],['e2fsck','-f','-n'])
            self.assertTrue(any(e.get('device_writes_started') for e in events))
            self.assertEqual(json.loads((root/'ce-journal/status.json').read_text())['phase'],'complete')

    def test_replay_failure_or_remaining_corruption_stops(self):
        for replay,after in ((4,0),(8,0),(0,4)):
            with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
                root=Path(tmp);r,_,calls=self.scenario(st,root,replay_code=replay,after_code=after)
                with self.assertRaises(ValueError):j.replay_if_needed(root,r,r['ota_repair']['partitions'],lambda *a,**kw:None)
                self.assertNotEqual(json.loads((root/'ce-journal/status.json').read_text())['phase'],'complete')
                self.assertFalse(any('-y' in a for a in calls))

    @unittest.skipUnless(all(shutil.which(x) for x in ('mkfs.ext4','debugfs','e2fsck')), 'needs ext4 tools')
    def test_actual_journal_only_and_undo_on_regular_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);img=root/'fs.img';undo=root/'undo'
            with img.open('wb') as f:f.truncate(64*1024**2)
            subprocess.run(['mkfs.ext4','-q','-F',str(img)],check=True,capture_output=True)
            subprocess.run(['debugfs','-w','-R','feature needs_recovery',str(img)],check=True,capture_output=True)
            raw=img.read_bytes()[1024:2048]
            self.assertTrue(struct.unpack_from('<I',raw,96)[0]&4)
            result=j.layout.run_fsck(['e2fsck','-p','-E','journal_only','-z',str(undo),str(img)],lambda *a,**k:None,'test',writing=True)
            self.assertIn(result.returncode,(0,1),(result.stdout,result.stderr))
            self.assertGreater(undo.stat().st_size,0)
            result=subprocess.run(['e2fsck','-f','-n',str(img)],capture_output=True)
            self.assertEqual(result.returncode,0,(result.stdout,result.stderr))
