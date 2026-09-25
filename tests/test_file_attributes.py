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
