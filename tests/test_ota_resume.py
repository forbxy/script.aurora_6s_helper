"""Offline OTA evidence, failure injection and sector-scope tests. No real disks."""
import contextlib,copy,hashlib,json,os,struct,sys,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import ota_resume_core as core
import ota_resume as resume
from ota_snapshot import Reader,exceptions,digest_snapshot
from test_emmc import report
import test_dual_repair as existing
import dual_repair as repair
import policy

FIX=Path(__file__).with_name('fixtures')/'ota_resume'

def misc(source='_a',recovery=True):
    raw=bytearray(2*1024**2);s='ab'.index(source[1]);t=1-s
    if recovery:
        raw[:13]=b'boot-recovery';raw[64:64+len(core.RECOVERY)]=core.RECOVERY
    raw[2048:2052]=source.encode()+b'\0\0';struct.pack_into('<I',raw,2052,0x42414342)
    raw[2056:2060]=b'\x01\x02\0\0';raw[2060+s*2]=0xf7;raw[2060+t*2]=0x77
    struct.pack_into('<I',raw,2076,zlib.crc32(raw[2048:2076]))
    raw[32768:32775]=bytes.fromhex('02b00a745602')+bytes([s])
    return bytes(raw)


def evidence():
    return {'state':2,'source':'_a','target':'_b','image':(FIX/'lp_metadata').read_bytes(),
            'snapshots':[core.snapshot((FIX/(n+'_b')).read_bytes(),n+'_b') for n in core.PARTITIONS]}


class ParserTests(unittest.TestCase):
    def test_real_metadata_and_variable_userdata_origin(self):
        r=report();target=policy.layouts(r)[2];target[-1]['size']=16*1024**3
        for offset in (45357203456,8000000000):
            target[-1]['offset']=offset
            maps=resume.mappings(evidence(),(FIX/'super.bin').read_bytes(),r,target)
            self.assertEqual(len(maps),5)
            self.assertEqual(maps[0]['cow'][0][0],offset+16622104*512)
        target[-1]['size']=12*1024**3
        with self.assertRaisesRegex(ValueError,'容量'):resume.mappings(evidence(),(FIX/'super.bin').read_bytes(),r,target)

    def test_android_confirmed_target_preserves_all_ab_bytes(self):
        for source in ('_a','_b'):
            raw=bytearray(misc(source));s='ab'.index(source[1]);t=1-s
            raw[2048:2052]=('_'+chr(97+t)).encode()+b'\0\0'
            raw[2060+s*2]=0x77;raw[2060+t*2]=0xf7
            struct.pack_into('<I',raw,2076,zlib.crc32(raw[2048:2076]))
            before=bytes(raw)
            self.assertEqual(core.boot_control(before,source),'target-confirmed')
            after=core.misc_candidate(before,source,False)
            self.assertEqual(after[32:],before[32:]);self.assertFalse(any(after[:32]))
            self.assertEqual(core.misc_candidate(after,source,False),after)
            with self.assertRaises(ValueError):core.misc_candidate(before,source,True)
            # A consumed/unsuccessful target cannot enter this preserve-success branch.
            raw[2060+t*2]=0x67;struct.pack_into('<I',raw,2076,zlib.crc32(raw[2048:2076]))
            with self.assertRaises(ValueError):core.boot_control(bytes(raw),source)

    def test_crc_bounds_conflicts(self):
        image=bytearray((FIX/'lp_metadata').read_bytes());image[8]^=1
        with self.assertRaises(ValueError):core.image_table(image,16*1024**3)
        superraw=bytearray((FIX/'super.bin').read_bytes());maximum,slots,_=core.metadata_geometry(superraw)
        # One valid copy can be used, two invalid copies cannot.
        for i in (1,1+slots):superraw[12288+i*maximum+12]^=1
        with self.assertRaises(ValueError):core.super_table(superraw,1800*1024**2,1)
        with self.assertRaises(ValueError):core.super_table(bytes(4096),1800*1024**2,1)
        for raw in (b'\x08\x80',b'\x08\x02\x08\x02',b'\x10\x01'):
            with self.assertRaises(ValueError):core.protobuf(raw,{1:0})

    def test_slot_and_bcb_scope_both_directions(self):
        for source in ('_a','_b'):
            old=misc(source);first=core.misc_candidate(old,source,False)
            self.assertEqual(first[32:],old[32:]);self.assertFalse(any(first[:32]))
            after=core.misc_candidate(first,source,True);t=1-'ab'.index(source[1])
            allowed={2060+t*2,2076,2077,2078,2079}
            self.assertTrue(all(i in allowed for i,(x,y) in enumerate(zip(first,after)) if x!=y))
            self.assertEqual(core.boot_control(after,source),'already-selected')
            with self.assertRaises(ValueError):core.misc_candidate(after,source,True)
            self.assertEqual(after[2060+(1-t)*2],0xf7)
            self.assertEqual(zlib.crc32(after[2048:2076]),struct.unpack_from('<I',after,2076)[0])

    def test_refuse_failed_attempts_conflicting_source_or_unknown_recovery(self):
        for at in (0,32,64,832,2048,2052,2060,2062,2076,32773,32774):
            raw=bytearray(misc());raw[at]^=1
            if at in (2048,2052,2060,2062):struct.pack_into('<I',raw,2076,zlib.crc32(raw[2048:2076]))
            with self.assertRaises(ValueError,msg=str(at)):core.misc_candidate(bytes(raw),'_a',True)
        # Do not refill a target's already-consumed attempt budget.
        raw=bytearray(misc());raw[2062]=0x5f;struct.pack_into('<I',raw,2076,zlib.crc32(raw[2048:2076]))
        with self.assertRaisesRegex(ValueError,'重试次数'):core.misc_candidate(raw,'_a',True)

    def test_records_refuse_missing_snapshot_symlinks_and_merging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'ota/snapshots').mkdir(parents=True);(root/'gsi/ota').mkdir(parents=True)
            (root/'ota/state').write_bytes(b'\x08\x02');(root/'ota/snapshot-boot').write_bytes(b'_a')
            (root/'gsi/ota/lp_metadata').write_bytes((FIX/'lp_metadata').read_bytes())
            for n in core.PARTITIONS:(root/'ota/snapshots'/ (n+'_b')).write_bytes((FIX/(n+'_b')).read_bytes())
            self.assertEqual(resume.records(root)['target'],'_b')
            (root/'ota/state').write_bytes(b'\x08\x03')
            with self.assertRaises(ValueError):resume.records(root)
            (root/'ota/state').write_bytes(b'\x08\x02');(root/'ota/snapshots/odm_b').unlink()
            with self.assertRaises(ValueError):resume.records(root)
            (root/'ota/snapshots/odm_b').symlink_to(FIX.resolve()/'odm_b')
            with self.assertRaises(ValueError):resume.records(root)

    def test_snapshot_overlay_and_corruption(self):
        old=b'A'*4096+b'B'*4096;cow=bytearray(4*4096)
        struct.pack_into('<4I',cow,0,0x70416e53,1,1,8);struct.pack_into('<QQ',cow,4096,1,2);cow[8192:12288]=b'C'*4096
        c=Reader(lambda o,n:cow[o:o+n],[(0,5000),(5000,len(cow)-5000)])
        v=digest_snapshot(Reader(lambda o,n:old[o:o+n],[(0,len(old))]),c,len(old))
        self.assertEqual(v['sha256'],hashlib.sha256(b'A'*4096+b'C'*4096).hexdigest())
        self.assertEqual(v['origin_chunks'],1)
        struct.pack_into('<QQ',cow,4096,1,1)
        with self.assertRaises(ValueError):exceptions(c,len(old))


class IntegrationTests(unittest.TestCase):
    setup_case = existing.PipelineTests.setup_case
    def test_snapshot_failure_prevents_layout_env_and_misc_writes(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
            root=Path(tmp);r,e,regions,m=self.setup_case(st,root,True)
            r['ota_repair']['ota_resume']={'state':'ready','mode':'layout-first'}
            st.enter_context(patch.object(repair.op,'assess',return_value=copy.deepcopy(r)))
            st.enter_context(patch.object(resume,'prepare',side_effect=ValueError('snapshot bad')))
            commit=st.enter_context(patch.object(resume,'commit'))
            with self.assertRaisesRegex(ValueError,'snapshot bad'):repair.execute(root,r,lambda *a,**k:None)
            m['layout'].assert_not_called();m['write'].assert_not_called();commit.assert_not_called()

    def test_target_retry_not_treated_as_noop_and_uses_verified_expected_env(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
            root=Path(tmp);r,e,regions,m=self.setup_case(st,root,count=29)
            r['ota_repair']['ota_resume']={'state':'ready','mode':'target-retry','target':'_b'}
            st.enter_context(patch.object(repair.op,'assess',return_value=copy.deepcopy(r)))
            def prepare(*args):
                (root/'ota-resume').mkdir();return b'candidate'
            st.enter_context(patch.object(resume,'prepare',side_effect=prepare))
            commit=st.enter_context(patch.object(resume,'commit'))
            result=repair.execute(root,r,lambda *a,**k:None)
            self.assertTrue(result['changed']);self.assertIn('6 次',result['message']);m['layout'].assert_not_called()
            self.assertEqual(commit.call_args.args[5],e)

    def test_stage1_does_not_activate_slot_and_requests_external_reboot(self):
        with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
            root=Path(tmp);r,e,regions,m=self.setup_case(st,root,True)
            r['ota_repair']['ota_resume']={'state':'ready','mode':'layout-first','target':'_b'}
            st.enter_context(patch.object(repair.op,'assess',return_value=copy.deepcopy(r)))
            order=[]
            def prepare(*args):order.append('snapshot-check');(root/'ota-resume').mkdir();return b'bcb-only'
            st.enter_context(patch.object(resume,'prepare',side_effect=prepare))
            def recover(*args,**kw):
                order.append('layout');p=root/'layout-repair';p.mkdir()
                for key,off,n in [('dtb',40*1024**2,524288),('mpt',36*1024**2,4096)]:
                    raw=('new-'+key).encode();regions[off,n]=raw;(p/('repair-'+key+'.bin')).write_bytes(raw)
            m['layout'].side_effect=recover
            m['write'].side_effect=lambda *a:order.append('env')
            commit=st.enter_context(patch.object(resume,'commit',side_effect=lambda *a:order.append('misc')))
            result=repair.execute(root,r,lambda *a,**k:None)
            self.assertEqual(order,['snapshot-check','layout','env','misc'])
            self.assertEqual(commit.call_args.args[1]['mode'],'layout-first')
            self.assertIn('A/B 槽位未修改',result['message']);self.assertIn('外置 CE',result['message'])

class WriteBoundaryTests(unittest.TestCase):
    def test_exact_sectors_and_full_misc_preservation(self):
        import stat,types
        for activate in (False,True):
            for source in ('_a','_b'):
                with tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as st:
                    r=report();row=next(p for p in r['mpt']['partitions'] if p['name']=='misc')
                    before=misc(source);after=core.misc_candidate(before,source,activate)
                    path=Path(tmp)/'disk'
                    with path.open('wb') as f:f.seek(row['offset']);f.write(before)
                    ev={'source':source,'mode':'target-retry' if activate else 'layout-first'}
                    metadata={'dtb':b'dtb','mpt':b'mpt'};env={'active_slot':'normal'}
                    for name in ('external','target_idle','raw_checkpoint'):st.enter_context(patch.object(resume.op,name))
                    st.enter_context(patch.object(resume,'guards'))
                    st.enter_context(patch.object(resume,'sources_idle'))
                    st.enter_context(patch.object(resume.op,'DISK',str(path)))
                    st.enter_context(patch.object(resume.layout,'read_at',side_effect=lambda off,n:metadata['dtb' if n==524288 else 'mpt']))
                    st.enter_context(patch.object(resume.layout,'env_read',return_value=env))
                    st.enter_context(patch.object(resume.os,'fstat',return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK)))
                    st.enter_context(patch.object(resume.fcntl,'ioctl',return_value=struct.pack('Q',r['emmc_bytes'])))
                    original=Path.read_text
                    st.enter_context(patch.object(Path,'read_text',lambda p,*a,**kw:r['cid'] if str(p)=='/sys/block/mmcblk0/device/cid' else original(p,*a,**kw)))
                    st.enter_context(patch.object(resume.os,'sync'))
                    write=st.enter_context(patch.object(resume.os,'pwrite',wraps=os.pwrite))
                    resume.commit(r,ev,before,after,metadata,env,lambda *a,**kw:None)
                    self.assertEqual([c.args[2] for c in write.call_args_list], [row['offset']]+([row['offset']+2048] if activate else []))
                    self.assertTrue(all(len(c.args[1])==512 for c in write.call_args_list))
                    with path.open('rb') as f:f.seek(row['offset']);self.assertEqual(f.read(),after)

    def test_changed_evidence_never_opens_writable_device(self):
        before=misc();after=core.misc_candidate(before,'_a',True)
        with patch.object(resume.op,'external'),patch.object(resume.op,'target_idle'),patch.object(resume.op,'raw_checkpoint'),patch.object(resume,'sources_idle'),patch.object(resume,'guards',side_effect=ValueError('metadata changed')),patch.object(resume.os,'open') as opened:
            with self.assertRaisesRegex(ValueError,'metadata changed'):resume.commit(report(),{'source':'_a','mode':'target-retry'},before,after,{}, {}, lambda *a,**kw:None)
            opened.assert_not_called()

    def test_submit_requires_exact_confirmed_ota_plan(self):
        import worker
        r=report();r['ota_repair']={'ota_resume':{'state':'ready','target':'_b'}}
        with patch.object(worker.op,'assess',return_value=r):
            for digest in (None,'0'*64):
                with self.assertRaisesRegex(ValueError,'确认界面'):worker.submit('repair',risk=True,repair_digest=digest)

if __name__=='__main__':unittest.main()
