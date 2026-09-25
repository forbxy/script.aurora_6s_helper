import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ADDON=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ADDON/'resources/emmc'))
import aurora_emmc
import policy
import operations as op
import worker
from layout_trial import write_regions, read_regions, REGIONS
from prepare_boot_environment import BOOTCMD, scan_through

# Public partition geometry only; no serials or user content in the fixtures.
def report(model='A4111',capacity=62537072640):
    names=['bootloader','reserved','cache','env','frp','factory','vendor_boot_a','vendor_boot_b','tee','logo','misc','dtbo_a','dtbo_b','cri_data','param','odm_ext_a','odm_ext_b','oem_a','oem_b','boot_a','boot_b','rsv','metadata','vbmeta_a','vbmeta_b','vbmeta_system_a','vbmeta_system_b','super']
    sizes=[4,64,0,8,2,8,24,24,32,8,2,2,2,8,16,16,16,32,32,64,64,16,16,2,2,2,2,1800]
    rows=[]
    for i,(name,size) in enumerate(zip(names,sizes)):
        off=0 if not i else 36*op.MIB if i==1 else rows[-1]['offset']+rows[-1]['size']+8*op.MIB
        rows.append(dict(index=i+1,name=name,offset=off,size=size*op.MIB,flags=policy.FIRST_FLAGS[i],padding=0,sysfs_matches=True))
    off=rows[-1]['offset']+rows[-1]['size']+8*op.MIB
    rows.append(dict(index=29,name='userdata',offset=off,size=capacity-off,flags=4,padding=0,sysfs_matches=True))
    return dict(schema=1,cid='a'*32,android_models=[model],os_release={'ID':'coreelec','DISTRO_DEVICE':'Amlogic-no','VERSION_ID':'22.0'},blockers=[],emmc_bytes=capacity,mpt={'partitions':rows},mpt_sha256='mpt',android_dtb_sha256='dtb',boot_areas={'mmcblk0boot0':4194304,'mmcblk0boot1':4194304})

class PolicyTests(unittest.TestCase):
    def test_both_models_and_capacities(self):
        for model,size in [('A4111',62537072640),('A4112',62545461248)]:
            r=report(model,size);self.assertEqual(policy.identity(r),model)
            kind,stock,dual=policy.layouts(r);self.assertEqual(kind,'stock');self.assertEqual(len(dual),31)
            self.assertEqual(dual[:28],[{k:v for k,v in x.items() if k in policy.FIELDS} for x in stock[:28]])
            r['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in dual]
            kind,recovered,_=policy.layouts(r);self.assertEqual(kind,'dual');self.assertEqual([[x[k] for k in policy.FIELDS] for x in stock],[[x[k] for k in policy.FIELDS] for x in recovered])
    def test_unknown_or_conflicting_model(self):
        for models in [[],['A4113'],['A4111','A4112'],['A4111','other']]:
            r=report();r['android_models']=models
            with self.assertRaises(ValueError):policy.identity(r)
    def test_ng_wrong_major_refused(self):
        r=report();r['os_release']['DISTRO_DEVICE']='Amlogic-ng'
        with self.assertRaises(ValueError):policy.identity(r)
    def test_geometry_and_flags_rejected(self):
        for key,value in [('offset',1),('size',1),('flags',99),('sysfs_matches',False)]:
            r=report();r['mpt']['partitions'][5][key]=value
            with self.assertRaises(ValueError):policy.layouts(r)
    def test_unsupported_dual_sizes_rejected(self):
        r=report();rows=policy.layouts(r)[2];rows[29]['size']-=op.MIB
        r['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in rows]
        with self.assertRaises(ValueError):policy.layouts(r)
    def test_mapping_never_reaches_system(self):
        for row in [dict(name='super',offset=0,size=4096),dict(name='ce_system',offset=1024,size=4096),dict(name='ce_storage',offset=8192,size=1),dict(name='userdata',offset=8192,size=65536)]:
            with self.assertRaises(ValueError):policy.bounded_region(row,32768,8192)
        self.assertEqual(policy.bounded_region(dict(name='ce_system',offset=8192,size=4096),32768,8192),(8192,4096))
    def test_restore_requires_same_chip_and_capacity(self):
        for key,value in [('cid','b'*32),('emmc_bytes',123),('android_models',['A4112']),('boot_areas',{})]:
            original=report();bad=copy.deepcopy(original);bad[key]=value
            with self.assertRaises(ValueError):policy.backup_matches(original,bad)
    def test_no_disclaimer_no_writes(self):
        for action in ('install','remove','restore'):
            for reset,risk in [(False,False),(True,False),(False,True)]:
                with patch.object(op,'assess') as assess:
                    with self.assertRaises(ValueError):worker.submit(action,reset=reset,risk=risk)
                    assess.assert_not_called()
    def test_env_only_scanner_changes(self):
        for count in (24,29,31):
            for source in (False,True):
                scan=scan_through(count)
                if source:scan=scan.replace('autoscr ${loadaddr}','source ${loadaddr}; autoscr ${loadaddr}')
                env=dict(bootcmd=BOOTCMD,bootfromemmc='run cfgloademmc',bootfromnand='0',active_slot='_a',storeboot='get_valid_slot; imgread kernel ${boot_part}',cfgloademmc=scan)
                before=copy.deepcopy(env)
                self.assertEqual(policy.scanner(policy.environment_change(env,'install')),(29,source))
                self.assertEqual(policy.scanner(policy.environment_change(env,'remove')),(24,source))
                self.assertEqual(before,env)
    def test_unknown_env_stops(self):
        with self.assertRaises(ValueError):policy.environment_change({'bootcmd':'boot'},'install')
        with self.assertRaises(ValueError):policy.scanner('run arbitrary')
    def test_internal_or_dm_boot_refused(self):
        r=report();r['mounts']={n:dict(dev='179:29',parent_disk='mmcblk0',fs='ext4') for n in ('/flash','/storage')}
        with patch.object(op,'depends_on_emmc',return_value=True):
            with self.assertRaisesRegex(ValueError,'外置'):op.external(r)

class WriteBoundaryTests(unittest.TestCase):
    def test_metadata_writer_never_changes_adjacent_bytes(self):
        old={name:b'A'*size for name,_,size in REGIONS};new={name:b'B'*size for name,_,size in REGIONS}
        with tempfile.TemporaryFile() as f:
            f.truncate(64*op.MIB)
            for name,offset,size in REGIONS:
                os.pwrite(f.fileno(),old[name],offset);os.pwrite(f.fileno(),b'Z'*512,offset-512);os.pwrite(f.fileno(),b'Z'*512,offset+size)
            write_regions(f.fileno(),old,new,lambda _:None)
            self.assertEqual(read_regions(f.fileno()),new)
            for _,offset,size in REGIONS:
                self.assertEqual(os.pread(f.fileno(),512,offset-512),b'Z'*512);self.assertEqual(os.pread(f.fileno(),512,offset+size),b'Z'*512)
    def test_changed_metadata_rejected_before_write(self):
        old={name:b'A'*size for name,_,size in REGIONS}
        with tempfile.TemporaryFile() as f:
            f.truncate(64*op.MIB)
            with patch('layout_trial.os.pwrite') as write:
                with self.assertRaises(ValueError):write_regions(f.fileno(),old,old,lambda _:None)
                write.assert_not_called()
    def test_failed_backup_hash_stops_restore_before_unmount_or_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(op,'private_external',return_value=Path(tmp)), patch.object(op,'inspect',side_effect=ValueError('hash mismatch')),patch.object(op,'detach_android') as detach:
            with self.assertRaises(ValueError):op.restore_images(tmp,report(),lambda *_:None)
            detach.assert_not_called()
    def test_background_job_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'request.json').write_text('{}');(p/'status.json').write_text('{"phase":"complete"}')
            with patch.object(worker,'private_external',return_value=p),patch('worker.open',create=True),patch('worker.fcntl.flock'):
                with self.assertRaisesRegex(ValueError,'重复'):worker.execute(p)

class RestoreTransferTests(unittest.TestCase):
    def test_restore_exact_range_and_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'source';b=Path(tmp)/'target';data=os.urandom(2*op.MIB+512)
            a.write_bytes(data);b.write_bytes(b'Z'*(len(data)+512))
            op.copy_image(a,b,len(data),hashlib.sha256(data).hexdigest())
            self.assertEqual(b.read_bytes(),data+b'Z'*512)
    def test_corrupt_backup_never_changes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'source';b=Path(tmp)/'target';a.write_bytes(b'bad');b.write_bytes(b'keep')
            with self.assertRaises(ValueError):op.copy_image(a,b,3,'0'*64)
            self.assertEqual(b.read_bytes(),b'keep')
    def test_truncated_backup_never_changes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'source';b=Path(tmp)/'target';a.write_bytes(b'x');b.write_bytes(b'keep')
            with self.assertRaises(ValueError):op.copy_image(a,b,9,hashlib.sha256(b'x').hexdigest())
            self.assertEqual(b.read_bytes(),b'keep')
    def test_symlink_backup_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'source';link=Path(tmp)/'link';b=Path(tmp)/'target';a.write_bytes(b'x');link.symlink_to(a);b.write_bytes(b'keep')
            with self.assertRaises(ValueError):op.copy_image(link,b,1,hashlib.sha256(b'x').hexdigest())
            self.assertEqual(b.read_bytes(),b'keep')

class JobOrderTests(unittest.TestCase):
    def test_full_backup_is_only_called_for_manual_backup(self):
        import contextlib
        for action in ('install','remove','restore','backup'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);job=root/'job';job.mkdir();r=report()
                (job/'request.json').write_text(json.dumps(dict(action=action,reset_accepted=True,risk_accepted=True,report=r,source_backup='chosen-backup',boot_id='testboot')))
                (job/'status.json').write_text(json.dumps(dict(phase='queued',boot_id='testboot',device_writes_started=False)))
                realopen=open
                def opened(path,*args,**kwargs):
                    if str(path).startswith('/run/aurora-emmc-'):return realopen(root/Path(path).name,*args,**kwargs)
                    return realopen(path,*args,**kwargs)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(worker,'private_external',return_value=job))
                    stack.enter_context(patch.object(worker,'read',return_value='testboot'))
                    stack.enter_context(patch('worker.open',side_effect=opened,create=True))
                    stack.enter_context(patch.object(op,'assess',return_value=r))
                    stack.enter_context(patch.object(op,'same_device'))
                    stack.enter_context(patch.object(worker,'inspect'))
                    backup=stack.enter_context(patch.object(op,'full_backup',return_value=root/'manual-backup'))
                    # Stop before reaching any destructive operation.
                    stack.enter_context(patch.object(policy,'layouts',side_effect=RuntimeError('stop before writes')))
                    if action=='backup':
                        worker.execute(job);backup.assert_called_once()
                    else:
                        with self.assertRaisesRegex(RuntimeError,'stop before writes'):worker.execute(job)
                        backup.assert_not_called()

    def test_remove_skips_backup_and_commits_environment_last(self):
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);job=root/'job';job.mkdir();r=report();r['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in policy.layouts(r)[2]]
            (job/'request.json').write_text(json.dumps(dict(action='remove',reset_accepted=True,risk_accepted=True,report=r,source_backup=None,boot_id='testboot')))
            (job/'status.json').write_text(json.dumps(dict(phase='queued',boot_id='testboot',device_writes_started=False)))
            disk=root/'disk'
            with disk.open('wb') as f:f.truncate(64*op.MIB)
            with disk.open('rb') as f:old=read_regions(f.fileno())
            events=[];realopen=open
            def opened(path,*args,**kwargs):
                if str(path).startswith('/run/aurora-emmc-'):return realopen(root/Path(path).name,*args,**kwargs)
                return realopen(path,*args,**kwargs)
            env=dict(bootcmd=BOOTCMD,bootfromemmc='run cfgloademmc',bootfromnand='0',active_slot='_a',storeboot='get_valid_slot; imgread kernel ${boot_part}',cfgloademmc=scan_through(29))
            with contextlib.ExitStack() as stack:
                for target,value in [('private_external',job),('read','testboot')]:stack.enter_context(patch.object(worker,target,return_value=value))
                stack.enter_context(patch('worker.open',side_effect=opened,create=True))
                stack.enter_context(patch.object(op,'DISK',str(disk)))
                for name,value in [('assess',r),('probe',r),('environment',env),('make_metadata',(old,old))]:stack.enter_context(patch.object(op,name,return_value=value))
                stack.enter_context(patch.object(op,'same_device'));stack.enter_context(patch.object(op,'target_idle'))
                backup_call=stack.enter_context(patch.object(op,'full_backup',side_effect=AssertionError('unexpected automatic backup')))
                stack.enter_context(patch.object(op,'zero_userdata',side_effect=lambda *a:events.append('wipe')))
                stack.enter_context(patch.object(op,'commit_metadata',side_effect=lambda *a:events.append('metadata')))
                stack.enter_context(patch.object(op,'change_environment',side_effect=lambda *a:events.append('environment')))
                worker.execute(job)
            backup_call.assert_not_called()
            self.assertEqual(events,['wipe','metadata','environment'])
            self.assertEqual(json.loads((job/'status.json').read_text())['phase'],'complete')

if __name__=='__main__':unittest.main()
