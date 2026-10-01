"""Persisted normal is an identity case, never permission to select a boot slot."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, mock_open
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import repair_identity as identity
import policy
import operations as op
from prepare_boot_environment import BOOTCMD,scan_through

class NormalInstallTests(unittest.TestCase):
    def environment(self):
        return dict(active_slot='normal',bootcmd=BOOTCMD,bootfromemmc='run cfgloademmc',
                    bootfromnand='0',cfgloademmc=scan_through(24),
                    storeboot='get_valid_slot;imgread kernel ${boot_part}')

    def test_normal_uses_b_model_after_a_geometry_failure_without_slot_choice(self):
        with patch.object(identity,'read_vendor_models',side_effect=[ValueError('vendor too short'),['A4111']]) as reader:
            models,evidence=identity.read_models_for_environment({},'active_slot=normal\n')
        self.assertEqual(models,['A4111'])
        self.assertEqual([c.kwargs['slot'] for c in reader.call_args_list],[0,1])
        self.assertEqual(evidence['persisted_active_slot'],'normal')
        self.assertNotIn('selected_slot',evidence)
        self.assertIn('error',evidence['slots']['_a'])

    def test_normal_conflict_unknown_models_and_cleanup_failure_still_stop(self):
        for answers,expected in (([['A4111'],['A4112']],ValueError),
                                 ([['OTHER'],['A4111']],ValueError),
                                 ([ValueError('A'),ValueError('B')],ValueError),
                                 ([RuntimeError('cleanup failed')],RuntimeError)):
            with self.subTest(answers=answers),patch.object(identity,'read_vendor_models',side_effect=answers),self.assertRaises(expected):
                identity.read_models_for_environment({},'active_slot=normal')

    def test_explicit_slot_keeps_same_slot_failure_and_does_not_fallback(self):
        for name,num in (('_a',0),('_b',1)):
            with patch.object(identity,'read_vendor_models',return_value=['A4111']) as reader:
                models,evidence=identity.read_models_for_environment({},'active_slot='+name)
            reader.assert_called_once_with({},slot=num)
            with patch.object(identity,'read_vendor_models',side_effect=ValueError('bad selected vendor')) as reader,self.assertRaises(ValueError):
                identity.read_models_for_environment({},'active_slot='+name)
            self.assertEqual(reader.call_count,1)

    def test_missing_unknown_or_multiple_values_do_not_read_vendor(self):
        for output in ('','active_slot=','active_slot=_c','normal','active_slot=normal\nactive_slot=_b'):
            with patch.object(identity,'read_vendor_models') as reader,self.assertRaises(ValueError):
                identity.read_models_for_environment({},output)
            reader.assert_not_called()

    def test_install_and_remove_write_only_scanner_and_preserve_normal(self):
        for action,count in (('install',29),('remove',24)):
            before=self.environment();saved=copy.deepcopy(before)
            after=dict(before,cfgloademmc=policy.environment_change(before,action))
            self.assertEqual(policy.scanner(after['cfgloademmc']),(count,False))
            with patch.object(op,'environment',side_effect=[before,after]),patch.object(op,'run') as run:
                op.change_environment(before,action)
            run.assert_called_once_with(['fw_setenv','cfgloademmc',after['cfgloademmc']])
            self.assertEqual(before,saved)
            with patch.object(op,'environment',side_effect=[before,dict(after,active_slot='_b')]),patch.object(op,'run'),self.assertRaisesRegex(ValueError,'读回'):
                op.change_environment(before,action)

    def test_unknown_slot_or_android_boot_entry_still_rejected(self):
        for key,value in (('active_slot',''),('active_slot','garbage'),('storeboot','run arbitrary'),
                          ('bootcmd','boot'),('cfgloademmc','echo custom')):
            env=self.environment();env[key]=value
            with self.assertRaises(ValueError):policy.environment_change(env,'install')


class HardwareNormalTests(unittest.TestCase):
    def read_hardware(self, output, answers, returncode=0):
        import read_hardware_model as hardware
        from types import SimpleNamespace
        import stat
        with patch.object(hardware.os, 'stat', return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=0)), \
             patch.object(hardware.Path, 'read_text', side_effect=['0:0', '100000']), \
             patch('builtins.open', mock_open(read_data=b'header')), \
             patch.object(hardware, 'parse_mpt', return_value={'partitions':[{'name':'super'}]}), \
             patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(returncode=returncode, stdout=output, stderr='read failed')) as command, \
             patch.object(identity, 'read_vendor_models', side_effect=answers) as reader:
            try:
                return hardware.read_models()
            finally:
                self.slots = [c.kwargs['slot'] for c in reader.call_args_list]
                command.assert_called_once_with(['fw_printenv', 'active_slot'], capture_output=True,
                                                encoding='utf-8', timeout=30)

    def test_hardware_normal_falls_back_to_readable_vendor(self):
        self.assertEqual(self.read_hardware('active_slot=normal', [ValueError('short A'), ['A4111']]), ['A4111'])
        self.assertEqual(self.slots, [0, 1])
        self.assertEqual(self.read_hardware('active_slot=normal', [['A4112'], ['A4112']]), ['A4112'])

    def test_hardware_explicit_slot_stays_explicit(self):
        self.assertEqual(self.read_hardware('active_slot=_b', [['A4111']]), ['A4111'])
        self.assertEqual(self.slots, [1])
        with self.assertRaises(ValueError):
            self.read_hardware('active_slot=_a', [ValueError('short A')])
        self.assertEqual(self.slots, [0])

    def test_hardware_rejects_conflict_and_environment_failure(self):
        with self.assertRaises(ValueError):
            self.read_hardware('active_slot=normal', [['A4111'], ['A4112']])
        with self.assertRaises(ValueError):
            self.read_hardware('active_slot=normal', [], returncode=1)
        self.assertEqual(self.slots, [])
        with self.assertRaises(ValueError):
            self.read_hardware('active_slot=unknown', [])
        self.assertEqual(self.slots, [])
