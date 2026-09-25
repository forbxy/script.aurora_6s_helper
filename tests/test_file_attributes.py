import os
from pathlib import Path
import tempfile
import unittest
from inspect_storage import scan_tree,rsync_flags
from file_attributes import attributes,copy_attributes
from live_snapshot import copy_live_storage
from stage_system import snapshot_inventory

class AttributeTests(unittest.TestCase):
    def test_rsync_fallback_preserves_root_file_dir_and_hardlink_attrs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';dest=Path(tmp)/'dest';source.mkdir();dest.mkdir()
            (source/'dir').mkdir();(source/'dir/file').write_bytes(b'content')
            os.link(source/'dir/file',source/'hardlink');(source/'link').symlink_to('dir/file')
            for path in (source,source/'dir',source/'dir/file'):os.setxattr(path,'user.test',b'\x00\xffattribute')
            (source/'aurora-emmc-backups').mkdir();(source/'aurora-emmc-backups/secret').write_text('excluded')
            inventory=scan_tree(source)
            flags=rsync_flags('rsync  version 3.4.1\nCapabilities: no ACLs, no xattrs',inventory,metadata_fallback=True).replace('n','')
            self.assertNotIn('X',flags);self.assertNotIn('A',flags)
            copy_live_storage(source,dest,flags,['/aurora-emmc-backups/'],None)
            self.assertEqual(attributes(dest/'dir/file'),{})
            os.setxattr(dest/'dir/file','user.stale',b'remove')
            copy_attributes(source,dest)
            for path in (Path('.'),Path('dir'),Path('dir/file'),Path('hardlink')):self.assertEqual(attributes(source/path),attributes(dest/path))
            self.assertFalse((dest/'aurora-emmc-backups').exists())
            self.assertEqual((dest/'dir/file').stat().st_ino,(dest/'hardlink').stat().st_ino)
    def test_inventory_detects_attribute_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);file=root/'file';file.write_bytes(b'unchanged')
            os.setxattr(file,'user.test',b'one')
            before=snapshot_inventory(root,include_xattrs=True)
            os.setxattr(file,'user.test',b'two')
            after=snapshot_inventory(root,include_xattrs=True)
            self.assertEqual(before['file']['sha256'],after['file']['sha256'])
            self.assertNotEqual(before,after)
    def test_capability_check_without_fallback_still_refuses_loss(self):
        inventory={'counts':{'other':0},'xattr_names':['user.test','system.posix_acl_access']}
        with self.assertRaises(ValueError):rsync_flags('rsync  version 3.4.1\nCapabilities: no ACLs, no xattrs',inventory)
        flags=rsync_flags('rsync  version 3.4.1\nCapabilities: no ACLs, no xattrs',inventory,metadata_fallback=True)
        self.assertEqual(flags,'-aHnx')

class SELinuxCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        from file_attributes import ignored_attribute_names
        ignored_attribute_names.cache_clear()

    def policy(self,os_id='coreelec',controls=(),lsm='',mounts='',unreadable=False):
        from unittest.mock import patch
        from file_attributes import ignored_attribute_names
        ignored_attribute_names.cache_clear()
        def read(path,*args,**kwargs):
            name=str(path)
            if name=='/etc/os-release':return 'ID="'+os_id+'"\n'
            if unreadable:raise PermissionError('cannot inspect SELinux runtime')
            if name=='/sys/kernel/security/lsm':return lsm
            if name=='/proc/self/mountinfo':return mounts
            raise AssertionError(name)
        with patch.object(Path,'read_text',read),patch.object(Path,'exists',lambda p:str(p) in controls or (str(p)=='/sys/kernel/security/lsm' and bool(lsm))):
            return ignored_attribute_names()

    def test_disabled_ce_ignores_only_selinux(self):
        self.assertEqual(self.policy(),('security.selinux',))

    def test_active_or_unknown_environment_preserves_labels(self):
        for options in [dict(os_id='android'),dict(controls=('/sys/fs/selinux/enforce',)),
                        dict(controls=('/selinux/enforce',)),dict(lsm='capability,selinux'),
                        dict(mounts='1 2 3:4 / /custom rw - selinuxfs selinuxfs rw\n'),dict(unreadable=True)]:
            with self.subTest(options=options):self.assertEqual(self.policy(**options),())

    def test_copy_and_inventory_ignore_residual_label_not_user_attrs(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';dest=Path(tmp)/'dest'
            for root in (source,dest):
                root.mkdir();(root/'dir').mkdir();(root/'dir/file').write_bytes(b'unchanged')
            for p in (source,source/'dir',source/'dir/file'):os.setxattr(p,'user.test',b'keep')
            listxattr=os.listxattr;getxattr=os.getxattr
            def names(path,**kwargs):
                return listxattr(path,**kwargs)+(['security.selinux'] if Path(path).is_relative_to(source) else [])
            def get(path,name,**kwargs):
                if name=='security.selinux':raise AssertionError('unused label should not be read or copied')
                return getxattr(path,name,**kwargs)
            with patch('file_attributes.ignored_attribute_names',return_value=('security.selinux',)),patch('os.listxattr',side_effect=names),patch('os.getxattr',side_effect=get):
                copy_attributes(source,dest)
                self.assertEqual(attributes(source),attributes(dest))
                self.assertEqual(snapshot_inventory(source,include_xattrs=True),snapshot_inventory(dest,include_xattrs=True))
            self.assertEqual(os.getxattr(dest,'user.test'),b'keep')
            self.assertEqual(os.getxattr(dest/'dir/file','user.test'),b'keep')

    def test_required_attributes_still_fail_with_attribute_name(self):
        import errno
        from unittest.mock import patch
        for name,ignored in [('security.selinux',()),('security.capability',('security.selinux',)),
                             ('system.posix_acl_access',('security.selinux',)),('user.test',('security.selinux',))]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                source=Path(tmp)/'source';dest=Path(tmp)/'dest';source.mkdir();dest.mkdir()
                with patch('file_attributes.ignored_attribute_names',return_value=ignored),patch('os.listxattr',side_effect=lambda p,**kw:[name] if Path(p)==source else []),patch('os.getxattr',return_value=b'data'),patch('os.setxattr',side_effect=OSError(errno.EOPNOTSUPP,'unsupported')):
                    with self.assertRaises(OSError) as ctx:copy_attributes(source,dest)
                    self.assertEqual(ctx.exception.errno,errno.EOPNOTSUPP)
                    self.assertIn(name,str(ctx.exception));self.assertIn(str(dest),str(ctx.exception))

    def test_rsync_native_xattrs_use_same_filter(self):
        from unittest.mock import patch
        from file_attributes import rsync_xattr_filters
        version='rsync  version 3.4.1\nCapabilities: ACLs, xattrs'
        inventory={'counts':{'other':0},'xattr_names':['security.selinux','user.test']}
        with patch('file_attributes.ignored_attribute_names',return_value=('security.selinux',)),patch('inspect_storage.ignored_attribute_names',return_value=('security.selinux',)):
            flags=rsync_flags(version,inventory,metadata_fallback=True).replace('n','')
            self.assertIn('X',flags);self.assertEqual(rsync_xattr_filters(flags),['--filter=-x security.selinux'])
            with tempfile.TemporaryDirectory() as tmp:
                source=Path(tmp)/'source';dest=Path(tmp)/'dest';source.mkdir();dest.mkdir()
                (source/'file').write_bytes(b'content');os.setxattr(source/'file','user.test',b'keep')
                copy_live_storage(source,dest,flags,[],None)
                self.assertEqual(os.getxattr(dest/'file','user.test'),b'keep')
            inventory['xattr_names']=['security.selinux']
            self.assertNotIn('X',rsync_flags(version,inventory,metadata_fallback=True))
        with patch('file_attributes.ignored_attribute_names',return_value=()):self.assertEqual(rsync_xattr_filters('-aHX'),[])
