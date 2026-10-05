"""NO initramfs guard, existing hooks and hardware repair rollback."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'resources/lib'))
import device
import repair
import wake_install

PROFILES = [('6s', 'rtl8852'), ('4pro', 'rtl8852'), ('4pro', 'ap6275p')]
SOURCE = repair.PAYLOAD / 'no/6s/post-sysroot.sh'


class NoBootHookTests(unittest.TestCase):
    def run_hook(self, dt_id, release='DISTRO_DEVICE="Amlogic-no"\n', coreelec=True, dt_present=True):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dt = root / 'device-tree'
            dt.mkdir()
            if coreelec:
                (dt / 'coreelec').write_bytes(b'')
            if dt_present:
                (dt / 'coreelec-dt-id').write_bytes(dt_id.encode() + b'\0')
            os_release = root / 'os-release'
            os_release.write_text(release)
            hook = root / 'post-sysroot.sh'
            hook.write_text(SOURCE.read_text().replace('/proc/device-tree', str(dt))
                            .replace('/sysroot/etc/os-release', str(os_release)))
            command = ('progress() { printf "%s\\n" "$*"; }; '
                       'check_amlogic_dtb() { echo STOCK_CHECK; }; '
                       '. "$1"; check_amlogic_dtb')
            result = subprocess.run(['sh', '-c', command, 'test', str(hook)],
                                    check=True, capture_output=True, text=True)
            if dt_present:
                self.assertEqual((dt / 'coreelec-dt-id').read_bytes(), dt_id.encode() + b'\0')
            return result.stdout

    def test_all_no_profiles_skip_the_filename_check(self):
        for key, dt_id in device.PROFILE_IDS.items():
            if key.startswith('no/'):
                with self.subTest(profile=key):
                    self.assertIn('Skipping stock DTB filename check', self.run_hook(dt_id))
        self.assertIn('Skipping stock DTB filename check', self.run_hook(
            device.PROFILE_IDS['no/6s'], release='COREELEC_DEVICE=Amlogic-no\n'))

    def test_other_dtbs_or_branches_keep_the_original_check(self):
        for dt_id in ['', 'sc2_s905x4', 'unknown', *device.PROFILE_IDS.values()]:
            if dt_id in [device.PROFILE_IDS[k] for k in device.PROFILE_IDS if k.startswith('no/')]:
                continue
            with self.subTest(dt_id=dt_id):
                self.assertEqual(self.run_hook(dt_id), 'STOCK_CHECK\n')
        dt_id = device.PROFILE_IDS['no/6s']
        for release in ['DISTRO_DEVICE="Amlogic-ng"\n', '', 'OTHER=Amlogic-no\n']:
            self.assertEqual(self.run_hook(dt_id, release=release), 'STOCK_CHECK\n')
        self.assertEqual(self.run_hook(dt_id, coreelec=False), 'STOCK_CHECK\n')
        self.assertEqual(self.run_hook(dt_id, dt_present=False), 'STOCK_CHECK\n')

    def test_payloads_match_and_ng_does_not_install_the_hook(self):
        for board, chip in PROFILES:
            with self.subTest(board=board, chip=chip):
                selected = repair.targets('no', board, chip)
                key = device.payload_key('no', board, chip) + '/post-sysroot.sh'
                self.assertEqual(selected[key], '/flash/post-sysroot.sh')
                self.assertEqual((repair.PAYLOAD / key).read_bytes(), SOURCE.read_bytes())
                self.assertNotIn('/flash/post-sysroot.sh', repair.targets('ng', board, chip).values())

    def test_existing_content_and_idempotent_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            hook = Path(temp) / 'post-sysroot.sh'
            initial = repair.boot_hook_content(hook, SOURCE)
            self.assertEqual(initial, SOURCE.read_bytes())
            # Preserve the user's earlier 6S-only script (and arbitrary bytes).
            existing = b'#!/bin/sh\r\n# custom \xff\r\nother_hook() { :; }'
            hook.write_bytes(existing)
            merged = repair.boot_hook_content(hook, SOURCE)
            self.assertTrue(merged.startswith(existing + b'\n'))
            suffix = b'# after helper\nother_hook\n'
            hook.write_bytes(merged + suffix)
            self.assertEqual(repair.boot_hook_content(hook, SOURCE), merged + suffix)
            # Replace an earlier managed block while preserving both sides.
            hook.write_bytes(existing + b'\n' + repair.BOOT_HOOK_BEGIN + b'\n: old\n'
                             + repair.BOOT_HOOK_END + b'\n' + suffix)
            self.assertEqual(repair.boot_hook_content(hook, SOURCE), merged + suffix)
            subprocess.run(['sh', '-n'], input=initial, check=True, capture_output=True)

    def test_damaged_markers_are_rejected_without_changing_the_script(self):
        for current in [repair.BOOT_HOOK_BEGIN + b'\n', repair.BOOT_HOOK_END + b'\n',
                        repair.BOOT_HOOK_END + b'\n' + repair.BOOT_HOOK_BEGIN + b'\n',
                        SOURCE.read_bytes() + SOURCE.read_bytes()]:
            with tempfile.TemporaryDirectory() as temp:
                hook = Path(temp) / 'post-sysroot.sh'
                hook.write_bytes(current)
                with self.assertRaisesRegex(RuntimeError, str(hook)):
                    repair.boot_hook_content(hook, SOURCE)
                self.assertEqual(hook.read_bytes(), current)

    def test_pre_hook_backup_schemas_keep_their_exact_allowlists(self):
        for branch in ('ng', 'no'):
            for board, chip in PROFILES:
                manifest = dict(branch=branch, board=board, chip=chip)
                for version in range(1, 9):
                    with self.subTest(branch=branch, board=board, chip=chip, version=version):
                        old = repair.backup_targets(dict(manifest, format=version))
                        self.assertNotIn('/flash/post-sysroot.sh', old.values())
                        if version in (4, 5, 6, 7, 8):
                            expected = {k: v for k, v in repair.targets(branch, board, chip).items()
                                        if not k.endswith('/post-sysroot.sh')}
                            self.assertEqual(old, expected)
                self.assertEqual(repair.backup_targets(dict(manifest, format=9)),
                                 repair.targets(branch, board, chip))

    def test_install_restore_and_failed_write_restore_original_hook(self):
        for board, chip in PROFILES:
            for existing in (None, b'#!/bin/sh\n# user hook\n: preserved\n'):
                for failure in (None, 'hook-write', 'reload'):
                    with self.subTest(board=board, chip=chip, existing=existing, failure=failure):
                        self.transaction(board, chip, existing, failure)

    def transaction(self, board, chip, existing, failure):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            local_no = {k: str(root / 'targets' / Path(v).name) for k, v in repair.NO.items()}
            profile = dict(branch='no', board=board, chip=chip)
            stack.enter_context(patch.object(repair, 'NO', local_no))
            selected = repair.profile_targets(profile)
            hook = root / 'targets/post-sysroot.sh'
            originals = {}
            for name, target in selected.items():
                path = Path(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                content = existing if path == hook else b'old ' + name.encode()
                if content is not None:
                    path.write_bytes(content)
                originals[path] = content
            for name, value in [('BACKUPS', root / 'backups'), ('check', lambda **kw: profile),
                                ('cleanup_targets', lambda *a: {}), ('links', lambda _: {}),
                                ('flash_is_ro', lambda: True)]:
                stack.enter_context(patch.object(repair, name, value))
            stack.enter_context(patch.object(wake_install, 'supported', return_value=False))
            stack.enter_context(patch.object(repair.os, 'sync'))
            failed = False
            real_copy = repair.atomic_copy

            def copy(source, target):
                nonlocal failed
                if failure == 'hook-write' and Path(target) == hook and not failed:
                    failed = True
                    raise OSError('simulated hook write error')
                return real_copy(source, target)

            def run(*args, **kwargs):
                nonlocal failed
                if failure == 'reload' and args == ('systemctl', 'daemon-reload') and not failed:
                    failed = True
                    raise OSError('simulated reload error')
                return SimpleNamespace(stdout='inactive', returncode=0)

            stack.enter_context(patch.object(repair, 'atomic_copy', side_effect=copy))
            stack.enter_context(patch.object(repair, 'run', side_effect=run))
            if failure:
                with self.assertRaisesRegex(RuntimeError, '已恢复原文件'):
                    repair.install()
                self.assertTrue(failed)
            else:
                backup = Path(repair.install())
                manifest = json.loads((backup / 'manifest.json').read_text())
                self.assertEqual(manifest['format'], 9)
                self.assertEqual(set(manifest['files']), set(selected))
                self.assertEqual(hook.read_bytes(), repair.boot_hook_content(hook, SOURCE))
                if existing:
                    self.assertTrue(hook.read_bytes().startswith(existing))
                repair.restore(backup)
            for path, content in originals.items():
                if content is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(path.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
