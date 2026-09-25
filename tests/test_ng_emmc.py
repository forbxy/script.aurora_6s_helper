import hashlib
import struct
import unittest
from platform_support import branch
from android_identity import vendor_extents
import policy
from test_emmc import report


def lp_image(extent_offset=2048,attrs=5):
    capacity=1800*1024**2
    part=struct.pack('<36sIIII',b'vendor_a',attrs,0,1,0)
    extent=struct.pack('<QIQI',128,0,extent_offset,0)
    group=struct.pack('<36sIQ',b'default',0,0)
    device=struct.pack('<QIIQ36sI',2048,1048576,0,capacity,b'super',0)
    table=part+extent+group+device
    header=bytearray(256);struct.pack_into('<IHHI',header,0,0x414c5030,10,2,256)
    struct.pack_into('<I',header,44,len(table));header[48:80]=hashlib.sha256(table).digest()
    offset=0
    for at,data in [(80,part),(92,extent),(104,group),(116,device)]:
        struct.pack_into('<III',header,at,offset,1,len(data));offset+=len(data)
    header[12:44]=hashlib.sha256(header).digest()
    geom=bytearray(struct.pack('<II32sIII',0x616c4467,52,bytes(32),65536,3,4096));geom[8:40]=hashlib.sha256(geom).digest()
    raw=bytearray(12288)+header+table;raw[4096:4148]=geom
    return raw,capacity

class NGTests(unittest.TestCase):
    def test_release_aliases_all_minor_versions(self):
        for version in ['21.0','21.3','21.99']:
            release={'ID':'coreelec','COREELEC_DEVICE':'Amlogic-ng','COREELEC_ARCH':'Amlogic-ng.arm','LIBREELEC_ARCH':'Amlogic-ng.arm','VERSION_ID':version}
            self.assertEqual(branch(release),'Amlogic-ng')
            r=report();r['os_release']=release;self.assertEqual(policy.identity(r),'A4111');policy.install_layout(r,16)
    def test_conflicting_or_unsupported_release_rejected(self):
        for release in [dict(ID='coreelec',DISTRO_DEVICE='Amlogic-no',COREELEC_DEVICE='Amlogic-ng',VERSION_ID='21.3'),dict(ID='coreelec',COREELEC_ARCH='Amlogic-ng.arm',VERSION_ID='22.0'),dict(ID='other',COREELEC_ARCH='Amlogic-ng.arm',VERSION_ID='21.3')]:
            with self.assertRaises(ValueError):branch(release)
    def test_vendor_extent_accepted(self):
        raw,size=lp_image();self.assertEqual(vendor_extents(raw,size),[(128,2048)])
    def test_corruption_refused(self):
        for offset in [4104,12320,12550]:
            raw,size=lp_image();raw[offset]^=1
            with self.assertRaisesRegex(ValueError,'checksum'):vendor_extents(raw,size)
    def test_extent_and_device_bounds(self):
        for offset in [1,1800*1024**2//512]:
            raw,size=lp_image(offset)
            with self.assertRaises(ValueError):vendor_extents(raw,size)
        raw,size=lp_image()
        with self.assertRaises(ValueError):vendor_extents(raw,size+512)
    def test_disabled_vendor_is_refused(self):
        raw,size=lp_image(attrs=9)
        with self.assertRaises(ValueError):vendor_extents(raw,size)
    def test_truncation_refused(self):
        raw,size=lp_image()
        for length in [0,4096,4147,12290,len(raw)-1]:
            with self.assertRaises(ValueError):vendor_extents(raw[:length],size)

class NGMappingTests(unittest.TestCase):
    def test_ng_dispatch_uses_dm_not_loop(self):
        import contextlib
        from unittest.mock import patch
        from pathlib import Path
        import operations as op
        r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
        row=r['mpt']['partitions'][-1]
        @contextlib.contextmanager
        def dm(*a,**kw):yield Path('/dev/mapper/test')
        with patch.object(op,'region_dm',side_effect=dm) as create,patch.object(op,'run') as run:
            with op.region_loop(row,r,readonly=True) as dev:self.assertEqual(str(dev),'/dev/mapper/test')
            create.assert_called_once_with(row['offset'],row['size'],True);run.assert_not_called()
    def test_dm_boundary_check_and_cleanup(self):
        import os
        from unittest.mock import patch,MagicMock
        import operations as op
        for wrong in [False,True]:
            calls=[]
            def run(args):
                calls.append(args)
                if args[:2]==['dmsetup','table']:return '0 8 linear 179:0 '+('17' if wrong else '16')
                return ''
            handle=MagicMock();handle.__enter__.return_value.fileno.return_value=3
            with patch.object(op.os,'stat',return_value=type('Info',(),{'st_mode':0o60600,'st_rdev':os.makedev(179,0)})()),patch.object(op,'run',side_effect=run),patch.object(op.Path,'open',return_value=handle),patch.object(op.fcntl,'ioctl',return_value=struct.pack('Q',4096)):
                if wrong:
                    with self.assertRaises(ValueError):
                        with op.region_dm(8192,4096):self.fail('Yielded invalid mapping')
                else:
                    with self.assertRaisesRegex(RuntimeError,'body failed'):
                        with op.region_dm(8192,4096,readonly=True):raise RuntimeError('body failed')
            self.assertEqual(calls[-1][:3],['dmsetup','remove','--retry'])
    def test_dm_size_mismatch_rejected(self):
        import os
        from unittest.mock import patch,MagicMock
        import operations as op
        handle=MagicMock();handle.__enter__.return_value.fileno.return_value=3
        with patch.object(op.os,'stat',return_value=type('Info',(),{'st_mode':0o60600,'st_rdev':os.makedev(179,0)})()),patch.object(op,'run',side_effect=['','0 8 linear 179:0 16','']) as run,patch.object(op.Path,'open',return_value=handle),patch.object(op.fcntl,'ioctl',return_value=struct.pack('Q',8192)):
            with self.assertRaisesRegex(ValueError,'容量'):
                with op.region_dm(8192,4096):self.fail('Yielded incorrect size')
            self.assertEqual(run.call_args.args[0][:3],['dmsetup','remove','--retry'])

class NGPostWriteTests(unittest.TestCase):
    def test_environment_update_uses_bounded_config_and_checks_all_keys(self):
        import contextlib
        from unittest.mock import patch
        import operations as op
        from prepare_boot_environment import BOOTCMD,scan_through
        env=dict(bootcmd=BOOTCMD,bootfromemmc='run cfgloademmc',bootfromnand='0',active_slot='_a',storeboot='get_valid_slot; imgread kernel ${boot_part}',cfgloademmc=scan_through(29))
        expected=dict(env,cfgloademmc=policy.environment_change(env,'remove'))
        @contextlib.contextmanager
        def options(r):yield ['-c','bounded-env.conf']
        with patch.object(op,'environment_options',side_effect=options),patch.object(op,'environment',side_effect=[env,expected]) as read,patch.object(op,'run') as run:
            op.change_environment(env,'remove',report())
            self.assertEqual(read.call_args.args,(['-c','bounded-env.conf'],))
            run.assert_called_once_with(['fw_setenv','-c','bounded-env.conf','cfgloademmc',expected['cfgloademmc']])
        with patch.object(op,'environment_options',side_effect=options),patch.object(op,'environment',side_effect=[env,dict(expected,active_slot='_b')]),patch.object(op,'run'):
            with self.assertRaisesRegex(ValueError,'读回'):op.change_environment(env,'remove',report())
    def test_ng_commit_does_not_reprobe_disappearing_partition_nodes(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import operations as op
        r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
        with tempfile.TemporaryDirectory() as tmp:
            disk=Path(tmp)/'disk';disk.touch()
            with patch.object(op,'DISK',str(disk)),patch.object(op,'raw_checkpoint') as check,patch.object(op,'target_idle'),patch.object(op,'probe',side_effect=AssertionError('Unexpected partition-node probe')),patch.object(op.stat,'S_ISBLK',return_value=True),patch.object(op,'write_regions') as write:
                op.commit_metadata(r,{'a':b'old'},{'a':b'new'},Path(tmp),lambda *a,**kw:None)
                check.assert_called_once_with(r);write.assert_called_once()
