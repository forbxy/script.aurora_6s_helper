"""Boot/layout dispatch and fault injection: never access a real device."""
import contextlib, copy, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import dual_repair as repair
import ota_repair as layout
import policy
from test_emmc import report
from test_ota_repair import env, misc, reader
from prepare_boot_environment import scan_through


def dual(branch='no'):
    r=report()
    r['mpt']['partitions']=[dict(p,sysfs_matches=True) for p in policy.layouts(r)[2]]
    r['os_release'].update(DISTRO_DEVICE='Amlogic-'+branch,VERSION_ID='22.0' if branch=='no' else '21.3')
    return r


class BootPolicyTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(repair.ota_resume,'inspect',return_value={'state':'none'})
        p.start();self.addCleanup(p.stop)

    def test_scanner_extension_and_preserved_source_variant(self):
        target=dual()['mpt']['partitions']
        for slot in ('normal','_a','_b'):
            for source in (False,True):
                e=env();e.update(active_slot=slot,cfgloademmc=scan_through(24))
                if source:e['cfgloademmc']=e['cfgloademmc'].replace('autoscr ${loadaddr}','source ${loadaddr}; autoscr ${loadaddr}')
                before=copy.deepcopy(e);changes=repair.environment_changes(e,target)
                self.assertEqual(set(changes),{'cfgloademmc'})
                self.assertEqual(policy.scanner(changes['cfgloademmc']),(29,source))
                self.assertEqual(e,before)
    def test_wider_scanner_is_never_shortened(self):
        for count in (29,31):
            e=env();e['cfgloademmc']=scan_through(count)
            self.assertEqual(repair.environment_changes(e,dual()['mpt']['partitions']),{})
    def test_unknown_scripts_or_pending_android_selection_rejected(self):
        for key,value in [('cfgloademmc','echo custom'),('bootcmd','run custom'),('bootfromnand','1'),('active_slot','garbage')]:
            e=env();e[key]=value
            with self.assertRaises(ValueError):repair.environment_changes(e,dual()['mpt']['partitions'])
    def test_dual_supported_both_branches(self):
        for branch in ('ng','no'):
            for count in (24,29):
                r=dual(branch);e=env();e['cfgloademmc']=scan_through(count)
                with patch.object(layout,'check_env_device'),patch.object(layout,'env_read',return_value=e),patch.object(layout,'read_at',return_value=misc()):
                    repair.assess(r)
                result=r['ota_repair']
                self.assertFalse(result['layout_needed'])
                self.assertEqual(bool(result['environment_changes']),count==24)
    def test_no_layout_combines_with_short_scanner(self):
        r=report();e=env();e['cfgloademmc']=scan_through(24)
        with patch.object(layout,'check_env_device'),patch.object(layout,'env_read',return_value=e),patch.object(layout,'read_at',side_effect=reader(r,20*1024**3)):
            repair.assess(r)
        self.assertTrue(r['ota_repair']['layout_needed'])
        self.assertEqual(set(r['ota_repair']['environment_changes']),{'cfgloademmc'})
    def test_ng_missing_layout_requires_valid_residual_filesystems(self):
        r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
        e=env();e['cfgloademmc']=scan_through(24)
        with patch.object(layout,'check_env_device'),patch.object(layout,'env_read',return_value=e),patch.object(layout,'read_at',side_effect=reader(r,20*1024**3)):
            repair.assess(r)
        self.assertTrue(r['ota_repair']['layout_needed'])
        self.assertEqual(r['ota_repair']['partitions'][-2]['size'],20*1024**3)
        with patch.object(layout,'read_at',return_value=bytes(4096)):
            with self.assertRaisesRegex(ValueError,'FAT32'):repair.assess(r)
    def test_pending_misc_is_reported_and_preserved_not_selected(self):
        r=dual();e=env();e['cfgloademmc']=scan_through(24);raw=bytearray(misc());raw[0]=1
        raw[32768:32774]=b'\x02\xb0\x0a\x74\x56\x02'
        with patch.object(layout,'check_env_device'),patch.object(layout,'env_read',return_value=e),patch.object(layout,'read_at',return_value=bytes(raw)):
            repair.assess(r)
        state=r['ota_repair']['android_state']
        self.assertTrue(state['pending_boot_message']);self.assertEqual(state['virtual_ab_status'],2)
        self.assertTrue(state['warnings']);self.assertNotIn('misc_selected_slot',r['ota_repair'])
        self.assertEqual(set(r['ota_repair']['environment_changes']),{'cfgloademmc'})


class PipelineTests(unittest.TestCase):
    def setup_case(self, stack, folder, needs_layout=False, count=24):
        r=report() if needs_layout else dual('ng')
        e=env();e['cfgloademmc']=scan_through(count)
        target=policy.layouts(r)[2] if needs_layout else r['mpt']['partitions']
        raw_misc=misc();raw_env=b'E'*65536
        regions={(40*1024**2,524288):b'DTB', (36*1024**2,4096):b'MPT'}
        for row in layout.preserved_rows(r):
            regions[row['offset'],row['size']]=raw_misc if row['name']=='misc' else raw_env
        r.update(android_dtb_sha256=repair.sha(b'DTB'),mpt_sha256=repair.sha(b'MPT'))
        r['ota_repair']=dict(layout_needed=needs_layout,environment_changes=repair.environment_changes(e,target),
                            environment_sha256=repair.sha(json.dumps(e,sort_keys=True).encode()),
                            misc_header_sha256=repair.sha(raw_misc),partitions=target)
        mocks={}
        mocks['journal']=stack.enter_context(patch.object(repair.ce_journal,'replay_if_needed',return_value=False))
        for name in ('same_device','external','target_idle'):
            mocks[name]=stack.enter_context(patch.object(repair.op,name))
        stack.enter_context(patch.object(repair.op,'assess',return_value=copy.deepcopy(r)))
        stack.enter_context(patch.object(layout,'env_read',return_value=e))
        mocks['read']=stack.enter_context(patch.object(layout,'read_at',side_effect=lambda off,n:regions[off,n]))
        stack.enter_context(patch.object(layout,'check_env_device'))
        stack.enter_context(patch.object(policy,'validate_dtb_layout'))
        mocks['fs']=stack.enter_context(patch.object(layout,'filesystem_check'))
        mocks['layout']=stack.enter_context(patch.object(layout,'execute'))
        mocks['write']=stack.enter_context(patch.object(repair,'write_environment'))
        stack.enter_context(patch.object(repair.os,'sync'))
        for name in ('make_metadata','zero_userdata','commit_metadata','full_backup'):
            stack.enter_context(patch.object(repair.op,name,side_effect=AssertionError('unexpected '+name)))
        return r,e,regions,mocks
    def test_boot_only_backs_up_and_never_rewrites_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);r,e,regions,m=self.setup_case(stack,root)
            result=repair.execute(root,r,lambda *a,**k:None)
            self.assertTrue(result['changed']);m['fs'].assert_called_once();m['layout'].assert_not_called()
            m['write'].assert_called_once_with(e,r['ota_repair']['environment_changes'],r)
            self.assertEqual((root/'boot-layout-repair/before-mpt.bin').read_bytes(),b'MPT')
            self.assertEqual(json.loads((root/'boot-layout-repair/environment-before.json').read_text()),e)
    def test_journal_failure_prevents_layout_and_environment_writes(self):
        for needs_layout in (False,True):
            with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                root=Path(tmp);r,e,regions,m=self.setup_case(stack,root,needs_layout)
                m['journal'].side_effect=ValueError('journal failed')
                with self.assertRaisesRegex(ValueError,'journal failed'):
                    repair.execute(root,r,lambda *a,**k:None)
                m['write'].assert_not_called();m['layout'].assert_not_called()
                m['fs'].assert_not_called()

    def test_normal_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);r,e,regions,m=self.setup_case(stack,root,count=31)
            self.assertFalse(repair.execute(root,r,lambda *a,**k:None)['changed'])
            m['write'].assert_not_called();m['layout'].assert_not_called();m['fs'].assert_not_called()
            self.assertFalse(list(root.iterdir()))
    def test_bad_payload_prevents_all_writes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);r,e,regions,m=self.setup_case(stack,root)
            m['fs'].side_effect=ValueError('Invalid SYSTEM header')
            with self.assertRaisesRegex(ValueError,'SYSTEM'):repair.execute(root,r,lambda *a,**k:None)
            m['write'].assert_not_called();m['layout'].assert_not_called()
    def test_metadata_change_during_checks_prevents_env_write(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);r,e,regions,m=self.setup_case(stack,root)
            m['fs'].side_effect=lambda *a:regions.update({(36*1024**2,4096):b'changed'})
            with self.assertRaisesRegex(ValueError,'元数据'):repair.execute(root,r,lambda *a,**k:None)
            m['write'].assert_not_called()
    def test_combined_runs_layout_before_env_and_verifies_result(self):
        for count in (24,31):
            with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                root=Path(tmp);r,e,regions,m=self.setup_case(stack,root,True,count)
                order=[]
                def recover(*args, **kwargs):
                    self.assertTrue(kwargs['boot_layout'])
                    order.append('layout');p=root/'layout-repair';p.mkdir()
                    for key,off,n in [('dtb',40*1024**2,524288),('mpt',36*1024**2,4096)]:
                        raw=('new-'+key).encode();regions[off,n]=raw;(p/('repair-'+key+'.bin')).write_bytes(raw)
                m['layout'].side_effect=recover;m['write'].side_effect=lambda *a:order.append('env')
                self.assertTrue(repair.execute(root,r,lambda *a,**k:None)['changed'])
                self.assertEqual(order,['layout','env'] if count==24 else ['layout'])
                m['fs'].assert_not_called()
    def test_environment_changed_after_preview_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root=Path(tmp);r,e,regions,m=self.setup_case(stack,root);e['active_slot']='_a'
            with self.assertRaisesRegex(ValueError,'启动环境'):repair.execute(root,r,lambda *a,**k:None)
            m['write'].assert_not_called();m['layout'].assert_not_called()
    def test_explicit_env_config_and_exact_full_readback(self):
        before=env();changes={'cfgloademmc':scan_through(29)}
        for after in (dict(before,**changes),dict(before,**changes,active_slot='_a')):
            with patch.object(layout,'read',return_value='/dev/env 0 0x10000 0x10000'),patch.object(layout,'env_read',side_effect=[before,after]),patch.object(layout,'run') as run:
                if after['active_slot']!=before['active_slot']:
                    with self.assertRaisesRegex(ValueError,'读回'):repair.write_environment(before,changes)
                else:repair.write_environment(before,changes)
                args=run.call_args.args[0]
                self.assertEqual(args[:2],['fw_setenv','-c']);self.assertEqual(args[3:],['cfgloademmc',changes['cfgloademmc']])
        with self.assertRaises(ValueError):repair.write_environment(before,{'active_slot':'_b'})

if __name__=='__main__':unittest.main()
