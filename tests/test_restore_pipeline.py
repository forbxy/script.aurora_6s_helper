import contextlib
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import operations as op
import worker
from test_emmc import report


class RestorePipelineTests(unittest.TestCase):
    def exercise(self, corrupt_last=False, main_force_ro=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rows=[];events=[]
            for i,name in enumerate(('mmcblk0.img','mmcblk0boot0.img','mmcblk0boot1.img')):
                data=bytes([i+1])*4096
                (root/name).write_bytes(data)
                target=root/('mmcblk0' if main_force_ro and i==0 else 'target-'+str(i));target.write_bytes(b'Z'*4096)
                rows.append(dict(file=name,source=str(target),bytes=4096,sha256=hashlib.sha256(data).hexdigest()))
            if corrupt_last:(root/rows[-1]['file']).write_bytes(b'X'*4096)
            def update(phase,message,**extra):events.append(('status',phase,extra))
            real_hash=op._hash_stream
            def hashed(stream,size,update,phase,name):
                events.append(('hash',phase,name))
                return real_hash(stream,size,update,phase,name)
            with contextlib.ExitStack() as stack:
                if main_force_ro:
                    sysroot=root/'sys';(sysroot/'mmcblk0').mkdir(parents=True)
                    (sysroot/'mmcblk0/force_ro').write_text('0')
                    stack.enter_context(patch.object(op,'Path',side_effect=lambda value:sysroot if str(value)=='/sys/block' else Path(value)))
                stack.enter_context(patch.object(op,'private_external',return_value=root))
                stack.enter_context(patch.object(op,'android_restore_session',side_effect=lambda update:contextlib.nullcontext(update)))
                metadata=stack.enter_context(patch.object(op,'inspect',return_value={'artifacts':rows}))
                stack.enter_context(patch.object(op,'read_identity',return_value=report()))
                stack.enter_context(patch.object(op,'probe',return_value=report()))
                stack.enter_context(patch.object(op,'same_device'))
                stack.enter_context(patch.object(op,'target_idle'))
                stack.enter_context(patch.object(op,'detach_android',side_effect=lambda *_:events.append(('detach',))))
                stack.enter_context(patch.object(op.fcntl,'ioctl',return_value=struct.pack('Q',4096)))
                stack.enter_context(patch.object(op,'_hash_stream',side_effect=hashed))
                if corrupt_last:
                    with self.assertRaisesRegex(ValueError,'校验失败'):op.restore_images(root,report(),update)
                    self.assertNotIn(('detach',),events)
                    self.assertFalse(any(e[0]=='status' and e[2].get('device_writes_started') for e in events))
                    for row in rows:self.assertEqual(Path(row['source']).read_bytes(),b'Z'*4096)
                else:
                    op.restore_images(root,report(),update)
                    self.assertEqual([e for e in events if e[0]=='hash'],
                        [('hash','verifying',r['file']) for r in rows]+
                        [('hash','readback',Path(r['source']).name) for r in rows])
                    detached=events.index(('detach',))
                    self.assertTrue(all(events.index(('hash','verifying',r['file']))<detached for r in rows))
                    for row in rows:self.assertEqual(Path(row['source']).read_bytes(),(root/row['file']).read_bytes())
                for call in metadata.call_args_list:self.assertFalse(call.kwargs.get('full_hash',False))

    def test_all_sources_verified_once_before_any_write(self):self.exercise()
    def test_main_force_ro_does_not_add_a_full_disk_comparison(self):self.exercise(main_force_ro=True)
    def test_corrupt_last_source_prevents_all_writes(self):self.exercise(corrupt_last=True)

    def test_source_change_after_verification_prevents_write(self):
        for mode in ('replace','modify','grow'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                source=Path(tmp)/'source';target=Path(tmp)/'target';source.write_bytes(b'abcd');target.write_bytes(b'keep')
                with op._verified_image(source,4,hashlib.sha256(b'abcd').hexdigest()) as image:
                    if mode=='replace':
                        other=Path(tmp)/'other';other.write_bytes(b'abcd');other.replace(source)
                    elif mode=='modify':
                        before=source.stat();source.write_bytes(b'xxxx')
                        # Do not depend on the test filesystem's timestamp resolution.
                        os.utime(source,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000_000))
                    else:
                        with source.open('ab') as f:f.write(b'extra')
                    with self.assertRaisesRegex(ValueError,'发生变化'):op._copy_verified_image(image,target)
                self.assertEqual(target.read_bytes(),b'keep')

    def test_modification_during_copy_is_detected_without_extra_source_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';target=Path(tmp)/'target';source.write_bytes(b'abcd');target.write_bytes(b'keep')
            def update(phase,message,**extra):
                if extra.get('device_writes_started'):source.write_bytes(b'xxxx')
            with op._verified_image(source,4,hashlib.sha256(b'abcd').hexdigest()) as image:
                with self.assertRaisesRegex(ValueError,'发生变化'):op._copy_verified_image(image,target,update)

    def test_readback_corruption_is_still_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';target=Path(tmp)/'target';source.write_bytes(b'abcd');target.write_bytes(b'keep')
            def update(phase,message,**extra):
                if phase=='readback':target.write_bytes(b'bad!')
            with op._verified_image(source,4,hashlib.sha256(b'abcd').hexdigest()) as image:
                with self.assertRaisesRegex(ValueError,'读回校验失败'):op._copy_verified_image(image,target,update)

    def test_worker_failure_state_distinguishes_prewrite_from_postwrite(self):
        for written in (False,True):
            with self.subTest(written=written),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);r=report()
                (root/'request.json').write_text(json.dumps(dict(action='restore',reset_accepted=True,risk_accepted=True,report=r,source_backup='backup',boot_id='testboot')))
                (root/'status.json').write_text(json.dumps(dict(phase='queued',boot_id='testboot',device_writes_started=False)))
                realopen=open
                def opened(path,*args,**kwargs):
                    if str(path).startswith('/run/aurora-emmc-'):return realopen(root/Path(path).name,*args,**kwargs)
                    return realopen(path,*args,**kwargs)
                def fail(folder,live,update):
                    update('verifying','verify')
                    if written:update('restoring','write',device_writes_started=True)
                    raise ValueError('injected failure')
                with contextlib.ExitStack() as stack:
                    stack.enter_context(patch.object(worker,'private_external',return_value=root))
                    stack.enter_context(patch.object(worker,'read',return_value='testboot'))
                    stack.enter_context(patch('worker.open',side_effect=opened,create=True))
                    stack.enter_context(patch.object(op,'assess',return_value=r))
                    stack.enter_context(patch.object(op,'same_device'))
                    inspect=stack.enter_context(patch.object(worker,'inspect',side_effect=AssertionError('duplicate verification')))
                    restore=stack.enter_context(patch.object(op,'restore_images',side_effect=fail))
                    with self.assertRaisesRegex(ValueError,'injected failure'):worker.execute(root)
                    inspect.assert_not_called();restore.assert_called_once()
                state=json.loads((root/'status.json').read_text())
                self.assertEqual(state['phase'],'needs-recovery' if written else 'failed')
                self.assertEqual(state['device_writes_started'],written)
