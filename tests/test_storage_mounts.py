"""Known runtime overlays must not become the installed boot-time config."""
import contextlib
import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'resources/emmc'))
import storage_mounts as sm


class StorageMountTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'storage';root.mkdir()
            base=(ROOT/'resources/payload/ng/6s/rtl8852bs_config').read_bytes()
            wake=(ROOT/'resources/wake/rtl8852bs_config.wake').read_bytes()
            for relative in (sm.SOURCE,sm.TARGET): (root/relative).parent.mkdir(parents=True,exist_ok=True)
            (root/sm.SOURCE).write_bytes(wake)
            os.link(root/sm.SOURCE,root/sm.TARGET)
            rows=[dict(id='1',device='8:2',root='/',path=str(root),options=['rw'],fs='ext4'),
                  dict(id='2',device='8:2',root='/'+str(sm.SOURCE),path=str(root/sm.TARGET),options=['rw'],fs='ext4')]
            calls=[];views=[]
            # tempfile.mkdtemp is patched below; keep the real function for fixture views.
            real_mkdtemp=tempfile.mkdtemp
            def mkdir(**kw):
                view=Path(real_mkdtemp(dir=tmp));views.append(view);return str(view)
            def command(args):
                calls.append(args)
                view=Path(args[-1])
                if args[:2]==['mount','--bind']:
                    shutil.copytree(root,view,dirs_exist_ok=True)
                    (view/sm.TARGET).write_bytes(base)
                    rows.append(dict(rows[0],id='3',path=str(view)))
                elif args[:2]==['mount','-o']: rows[-1]['options']=['ro']
                elif args[0]=='umount':
                    rows.pop()
                    for child in view.iterdir():
                        if child.is_dir():shutil.rmtree(child)
                        else:child.unlink()
            with patch.object(sm,'records',side_effect=lambda:copy.deepcopy(rows)), \
                 patch.object(sm.tempfile,'mkdtemp',side_effect=mkdir), \
                 patch.object(sm,'command',side_effect=command):
                yield root,rows,calls,views,base,wake

    def test_no_nested_mount_uses_original_without_commands(self):
        with self.fixture() as (root,rows,calls,*_):
            rows.pop()
            with sm.storage_source(root) as source:self.assertEqual(source,root)
            self.assertEqual(calls,[])

    def test_known_overlay_copies_base_and_preserves_live_wake(self):
        with self.fixture() as (root,rows,calls,views,base,wake):
            with sm.storage_source(root) as source:
                self.assertNotEqual(source,root)
                self.assertEqual((source/sm.TARGET).read_bytes(),base)
                self.assertEqual((source/sm.SOURCE).read_bytes(),wake)
                self.assertEqual((root/sm.TARGET).read_bytes(),wake)
            self.assertEqual([x[:2] for x in calls],[['mount','--bind'],['mount','--make-private'],['mount','-o'],['umount',str(views[0])]])
            self.assertEqual(len(rows),2)
            self.assertFalse(views[0].exists())
            self.assertEqual((root/sm.TARGET).read_bytes(),wake)

    def test_unknown_same_device_mount_and_unexpected_wake_sources_refused(self):
        for changes in ({'path':'/extra mount'}, {'root':'/wrong-source'},
                        {'device':'8:3'}, {'fs':'tmpfs'}):
            with self.subTest(changes=changes),self.fixture() as (root,rows,calls,*_):
                if 'path' in changes:changes=dict(path=str(root)+changes['path'])
                rows[1].update(changes)
                with self.assertRaisesRegex(ValueError,'Nested storage mount requires review:') as caught:
                    with sm.storage_source(root):self.fail('unexpected accepted mount')
                self.assertIn(rows[1]['path'],str(caught.exception));self.assertEqual(calls,[])

    def test_same_name_wrong_content_or_inode_refused(self):
        for mode in ('content','inode','symlink'):
            with self.subTest(mode=mode),self.fixture() as (root,rows,calls,views,base,wake):
                if mode=='content':(root/sm.SOURCE).write_bytes(b'wrong')
                else:
                    (root/sm.TARGET).unlink()
                    if mode=='inode':(root/sm.TARGET).write_bytes(wake)
                    else:(root/sm.TARGET).symlink_to(root/sm.SOURCE)
                with self.assertRaises(ValueError):
                    with sm.storage_source(root):self.fail('unverified overlay')
                self.assertEqual(calls,[])

    def test_known_overlay_does_not_hide_another_nested_mount(self):
        with self.fixture() as (root,rows,calls,*_):
            other=str(root/'videos/115open')
            rows.append(dict(rows[1],id='4',path=other,root='/another'))
            with self.assertRaises(ValueError) as caught:
                with sm.storage_source(root):self.fail('unknown mount accepted')
            self.assertIn(other,str(caught.exception));self.assertEqual(calls,[])

    def test_copy_failure_still_unmounts_view_only(self):
        with self.fixture() as (root,rows,calls,views,*_):
            with self.assertRaisesRegex(RuntimeError,'copy failed'):
                with sm.storage_source(root):raise RuntimeError('copy failed')
            self.assertEqual(calls[-1],['umount',str(views[0])])
            self.assertFalse(views[0].exists());self.assertEqual(len(rows),2)

    def test_bad_base_config_refused_and_view_cleaned(self):
        with self.fixture() as (root,rows,calls,views,*_):
            with patch.object(sm,'CONFIG_HASH','invalid'),self.assertRaisesRegex(ValueError,'基础配置'):
                with sm.storage_source(root):self.fail('bad base accepted')
            self.assertFalse(views[0].exists());self.assertEqual(len(rows),2)

    def test_readonly_remount_failure_still_cleans_up(self):
        with self.fixture() as (root,rows,calls,views,*_):
            original=sm.command.side_effect
            def fail(args):
                if args[:2]==['mount','-o']:raise RuntimeError('remount failed')
                return original(args)
            with patch.object(sm,'command',side_effect=fail),self.assertRaisesRegex(RuntimeError,'remount failed'):
                with sm.storage_source(root):self.fail('writable view')
            self.assertFalse(views[0].exists());self.assertEqual(len(rows),2)

    def test_mountinfo_escaped_paths_are_decoded(self):
        text='42 1 8:2 /some\\040folder /storage/a\\040b rw - ext4 /dev/sda2 rw\n'
        with patch.object(sm.Path,'read_text',return_value=text):
            self.assertEqual(sm.records()[0]['path'],'/storage/a b')


if __name__=='__main__':unittest.main()
