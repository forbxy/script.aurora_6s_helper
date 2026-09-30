"""Repair-only identity and opaque Android state; no device access."""
import contextlib, copy, sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import repair_identity as identity
import operations as op
import ota_repair as layout
from ota_repair_core import android_state, sha
from test_emmc import report
from test_ota_repair import misc, env, reader

class IdentityTests(unittest.TestCase):
    def test_vendor_identity_does_not_choose_boot_slot(self):
        for answers in ((['A4111'],['A4111']), (ValueError('bad A ext4 bounds'),['A4112'])):
            with patch.object(identity,'read_vendor_models',side_effect=answers) as read:
                found,evidence=identity.read_models({'name':'super'})
            self.assertEqual(found,answers[1])
            self.assertEqual([c.kwargs for c in read.call_args_list],[{'slot':0},{'slot':1}])
            self.assertNotIn('selected_slot',evidence)
    def test_conflicting_unknown_missing_or_unreadable_model_rejected(self):
        for answers in ((['A4111'],['A4112']),(['A4111'],['OTHER']),([],['A4111']),
                        (ValueError('bad A'),ValueError('bad B'))):
            with patch.object(identity,'read_vendor_models',side_effect=answers),self.assertRaises(ValueError):
                identity.read_models({})
    def test_cleanup_failure_cannot_be_used_as_fallback(self):
        with patch.object(identity,'read_vendor_models',side_effect=RuntimeError('cleanup failed')) as read:
            with self.assertRaisesRegex(RuntimeError,'cleanup'):identity.read_models({})
        self.assertEqual(read.call_count,1)
    def test_only_repair_uses_slot_independent_probe(self):
        class Stop(Exception):pass
        for action in (None,'install','remove','backup','restore','repair'):
            with patch.object(op,'probe',side_effect=Stop) as probe,self.assertRaises(Stop):op.assess(action)
            if action=='repair':probe.assert_called_once_with(repair_identity=True)
            else:probe.assert_called_once_with()
    def test_unknown_misc_is_opaque_not_guessed(self):
        state=android_state(b'X'*65536)
        self.assertIsNone(state['recorded_suffix']);self.assertFalse(state['ab_metadata_valid'])
        self.assertEqual(state['misc_sha256'],sha(b'X'*65536));self.assertTrue(state['warnings'])
        with self.assertRaisesRegex(ValueError,'Short'):android_state(b'X')

class LayoutInspectionTests(unittest.TestCase):
    def test_ng_combined_inspection_preserves_pending_ota(self):
        r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
        e=env();m=bytearray(misc('_a'));m[0]=1;m[32768:32774]=b'\x02\xb0\x0a\x74\x56\x02';m=bytes(m)
        mo=next(p['offset'] for p in r['mpt']['partitions'] if p['name']=='misc')
        residual=reader(r,20*1024**3);original={'dtb':b'DTB','mpt':b'MPT'}
        def read_at(off,n):
            if off==40*1024**2:return original['dtb']
            if off==36*1024**2:return original['mpt']
            if off==mo:return m
            return residual(off,n)
        with contextlib.ExitStack() as stack:
            probe=stack.enter_context(patch.object(op,'probe',return_value=r))
            for name in ('external','target_idle'):stack.enter_context(patch.object(op,name))
            stack.enter_context(patch.object(layout,'check_env_device'))
            stack.enter_context(patch('builtins.open',unittest.mock.mock_open()))
            stack.enter_context(patch.object(layout,'read_regions',return_value=original))
            stack.enter_context(patch.object(layout.policy,'validate_dtb_layout'))
            stack.enter_context(patch.object(layout,'env_read',return_value=e))
            stack.enter_context(patch.object(layout,'read_at',side_effect=read_at))
            fs=stack.enter_context(patch.object(layout,'filesystem_check',return_value={'boot_files':{}}))
            stack.enter_context(patch.object(layout,'select_slot',side_effect=AssertionError('must not select Android slot')))
            got,current,info=layout.inspect_state(lambda *a,**k:None,'repair-prepare',boot_layout=True)
        probe.assert_called_once_with(repair_identity=True);fs.assert_called_once()
        self.assertTrue(info['boot_layout']);self.assertEqual(info['environment_changes'],{})
        self.assertEqual(info['environment_before']['active_slot'],'normal')
        self.assertIsNone(info['slot']);self.assertEqual(info['android_state']['misc_sha256'],sha(m));self.assertEqual(current,original)

class RawEnvironmentTests(unittest.TestCase):
    def test_ng_environment_after_node_loss_uses_bounded_mapping(self):
        r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
        for readonly in (True,False):
            @contextlib.contextmanager
            def mapped(*args,**kwargs):yield Path('/dev/mapper/test-env')
            with patch.object(layout,'read',return_value='/dev/env 0 0x10000 0x10000'),patch.object(op,'raw_checkpoint') as check,patch.object(op,'region_dm',side_effect=mapped) as region,patch.object(layout,'require_readonly') as ro:
                with layout.environment_config(r,readonly=readonly) as config:
                    self.assertEqual(config.read_text(),'/dev/mapper/test-env 0x0 0x10000 0x10000\n')
            row=r['mpt']['partitions'][3]
            region.assert_called_once_with(row['offset'],row['size'],readonly=readonly)
            check.assert_called_once_with(r);self.assertEqual(ro.call_count,int(readonly))
    def test_no_environment_stays_on_existing_named_device(self):
        with patch.object(layout,'read',return_value='/dev/env 0 0x10000 0x10000'),patch.object(op,'region_dm',side_effect=AssertionError('NO must not use NG env mapping')):
            with layout.environment_config(report()) as config:
                self.assertEqual(config.read_text(),'/dev/env 0x0 0x10000 0x10000\n')

if __name__=='__main__':unittest.main()
