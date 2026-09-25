import contextlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'resources/lib'))
import device


class DeviceIdentityTests(unittest.TestCase):
    def detect(self, branch='ng', board='6s', revision='D', pci='', **kwargs):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            release = Path(tmp) / 'os-release'
            release.write_text('ID=coreelec\nCOREELEC_DEVICE=Amlogic-%s\nVERSION_ID=%s\n' %
                               (branch, '21.3' if branch == 'ng' else '22.0'))
            stack.enter_context(patch.object(device, 'RELEASE', release))
            stack.enter_context(patch.object(device, 'android_board',
                side_effect=board if isinstance(board, Exception) else None,
                return_value=board if isinstance(board, str) else ''))
            stack.enter_context(patch.object(device, 'soc_revision', return_value=revision))
            stack.enter_context(patch.object(device, 'pci_chip', return_value=pci))
            stack.enter_context(patch.object(device.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(device, 'text_property', side_effect=lambda name: {
                'compatible':'amlogic,sc2', 'model':'Tencent Aurora 4 Pro (A4111, AP6275P, CoreELEC NG)',
                'coreelec-dt-id':device.PROFILE_IDS['ng/4pro/ap6275p']}[name]))
            return device.detect(check_kernel=False, **kwargs)

    def test_original_identity_overrides_borrowed_dtb_before_wifi(self):
        for branch in ('ng', 'no'):
            result = self.detect(branch=branch)
            self.assertEqual((result['board'], result['chip'], result['payload']),
                             ('6s', 'rtl8852', branch+'/6s'))
            self.assertEqual(result['board_source'], 'android')
            self.assertFalse(result['confirmation_required'])

    def test_4pro_revision_matrix_without_pci(self):
        for branch in ('ng', 'no'):
            for revision in 'ABCD':
                chip = 'rtl8852' if revision == 'D' else 'ap6275p'
                result = self.detect(branch, '4pro', revision, confirmed_board='4pro')
                self.assertEqual(result['payload'], branch+'/4pro/'+chip)
                self.assertEqual(result['chip_source'], 'soc_revision')

    def test_6s_chip_does_not_depend_on_revision(self):
        self.assertEqual(self.detect(revision='A')['chip'], 'rtl8852')

    def test_pci_contradiction_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'PCI'):
            self.detect(board='4pro', revision='C', pci='rtl8852')
        with self.assertRaisesRegex(RuntimeError, 'PCI'):
            self.detect(pci='ap6275p')

    def test_pci_fallback_only_for_unknown_revision(self):
        self.assertEqual(self.detect(board='4pro', revision='', pci='ap6275p')['chip_source'], 'pci')
        with self.assertRaisesRegex(RuntimeError, '无法确定'):
            self.detect(board='4pro', revision='')

    def test_unreadable_identity_does_not_trust_known_dtb(self):
        missing = device.AndroidIdentityUnavailable('unreadable')
        result = self.detect(board=missing, allow_unidentified=True)
        self.assertEqual(result['board'], '')
        self.assertTrue(result['confirmation_required'])
        result = self.detect(board=missing, confirmed_board='6s')
        self.assertEqual(result['board_source'], 'manual')
        self.assertFalse(result['board_verified'])
        with self.assertRaisesRegex(RuntimeError, 'Android'):
            self.detect(confirmed_board='4pro')

    def test_mounted_models_need_no_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            props = Path(tmp)/'build.prop'
            props.write_text('ro.product.vendor.model=A4112\n')
            with patch.object(device, 'ANDROID_PROPERTIES', (props,)), patch.object(device.subprocess, 'run') as run:
                self.assertEqual(device.android_board(), '6s')
                run.assert_not_called()

    def test_mapping_models_and_conflicts(self):
        for payload, expected in [('["A4111"]', '4pro'), ('["A4112"]', '6s'),
                                  ('["A4111", "A4112"]', None), ('["other"]', None)]:
            with patch.object(device, 'ANDROID_PROPERTIES', ()), patch.object(device.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, payload, '')):
                if expected:
                    self.assertEqual(device.android_board(), expected)
                else:
                    with self.assertRaises(RuntimeError):device.android_board()

    def test_mapping_failure_allows_only_explicit_confirmation(self):
        with patch.object(device, 'ANDROID_PROPERTIES', ()), patch.object(device.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 1, '', 'no super')):
            with self.assertRaises(device.AndroidIdentityUnavailable):device.android_board()

    def test_a4111_d_requires_choice_even_with_known_dtb(self):
        for branch in ('ng', 'no'):
            result = self.detect(branch, '4pro', 'D', allow_unidentified=True)
            self.assertEqual(result['board'], '')
            self.assertTrue(result['model_choice_required'])
            with self.assertRaises(RuntimeError):self.detect(branch, '4pro', 'D')
            for selected in ('4pro', '6s'):
                result = self.detect(branch, '4pro', 'D', confirmed_board=selected)
                self.assertEqual(result['payload'], branch+'/'+selected+('/rtl8852' if selected=='4pro' else ''))
                self.assertEqual(result['board_source'], 'manual')

    def test_model_override_only_applies_to_a4111_d(self):
        for revision in 'ABC':
            with self.assertRaises(RuntimeError):self.detect(board='4pro', revision=revision, confirmed_board='6s')
        with self.assertRaises(RuntimeError):self.detect(board='6s', confirmed_board='4pro')
        with self.assertRaises(RuntimeError):self.detect(board='4pro', revision='D', pci='ap6275p', allow_unidentified=True)

    def test_runtime_lighting_uses_installed_variant_only(self):
        for selected in ('4pro', '6s'):
            with patch.dict(device.PROFILE_IDS, {'ng/'+selected+('/rtl8852' if selected=='4pro' else ''):
                                              device.PROFILE_IDS['ng/4pro/ap6275p']}):
                result = self.detect(board='4pro', revision='D', runtime_profile=True)
                self.assertEqual(result['board'], selected)
                self.assertEqual(result['board_source'], 'installed_dtb')
