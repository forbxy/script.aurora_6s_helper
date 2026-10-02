"""Changing an existing USB install from RTL8852 to AP6275P."""
import json
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'resources/lib'))
import repair


class WakeMigrationTests(unittest.TestCase):
    def test_legacy_restore_allowlists_unchanged(self):
        for version in (5, 6, 7):
            self.assertEqual(len(repair.cleanup_targets('ng', 'ap6275p', version)), 4)
            self.assertEqual(repair.cleanup_targets('no', 'ap6275p', version), {})
        for branch in ('ng', 'no'):
            self.assertEqual(repair.cleanup_targets(branch, 'rtl8852'), {})
            self.assertIn('cleanup/wake-suspend', repair.cleanup_targets(branch, 'ap6275p'))

    def test_migration_and_restore(self):
        for branch in ('ng', 'no'):
            for failure in (False, True):
                with self.subTest(branch=branch, failure=failure):
                    self.migrate(branch, failure)

    def migrate(self, branch, failure):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            systemd = root / 'systemd'
            payload = root / 'payload'
            payload.mkdir()
            (payload / 'dtb.img').write_bytes(b'new-ap-dtb')
            dtb = root / 'dtb.img'
            dtb.write_bytes(b'old-dtb')
            profile = dict(branch=branch, board='4pro', chip='ap6275p')
            for name, value in (
                ('SYSTEMD', systemd), ('PAYLOAD', payload), ('BACKUPS', root / 'backups'),
                ('NG', {k: str(root / Path(v).name) for k, v in repair.NG.items()}),
                ('check', lambda **kw: profile),
                ('profile_targets', lambda _: {'dtb.img': str(dtb)}),
                ('backup_targets', lambda _: {'dtb.img': str(dtb)}),
                ('links', lambda _: {}), ('flash_is_ro', lambda: True),
            ):
                stack.enter_context(patch.object(repair, name, value))
            removed = repair.cleanup_targets(branch, 'ap6275p')
            for key, filename in removed.items():
                path = Path(filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                if key == 'cleanup/wake-enable':
                    path.symlink_to(removed['cleanup/wake-service'])
                else:
                    path.write_text('saved ' + key)
            unrelated = systemd / 'systemd-suspend.service.d/other.conf'
            unrelated.write_text('unrelated')
            failed = False
            calls = []
            def run(*args, **kwargs):
                nonlocal failed
                calls.append(args)
                if args == ('systemctl', 'stop', 'aurora6s-wake.service'):
                    self.assertEqual(kwargs['timeout'], 105)
                    self.assertEqual(dtb.read_bytes(), b'old-dtb')
                if args == ('systemctl', 'daemon-reload') and failure and not failed:
                    self.assertFalse(Path(removed['cleanup/wake-suspend']).exists())
                    failed = True
                    raise OSError('simulated reload failure')
                return SimpleNamespace(stdout='inactive', returncode=0)
            stack.enter_context(patch.object(repair, 'run', side_effect=run))
            if failure:
                with self.assertRaisesRegex(RuntimeError, '已恢复原文件'):
                    repair.install()
            else:
                backup = Path(repair.install())
                self.assertEqual(json.loads((backup/'manifest.json').read_text())['format'], 8)
                self.assertEqual(dtb.read_bytes(), b'new-ap-dtb')
                for filename in removed.values():
                    self.assertFalse(Path(filename).exists())
                    self.assertFalse(Path(filename).is_symlink())
                repair.restore(backup)
            self.assertIn(('systemctl', 'stop', 'aurora6s-wake.service'), calls)
            self.assertEqual(dtb.read_bytes(), b'old-dtb')
            self.assertEqual(unrelated.read_text(), 'unrelated')
            for key, filename in removed.items():
                path = Path(filename)
                if key == 'cleanup/wake-enable':
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(str(path.readlink()), removed['cleanup/wake-service'])
                else:
                    self.assertEqual(path.read_text(), 'saved ' + key)


if __name__ == '__main__':
    unittest.main()
