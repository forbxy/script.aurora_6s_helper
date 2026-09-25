import errno
import hashlib
import importlib.util
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

EMMC = Path(__file__).resolve().parents[1]/'resources/emmc'
sys.path.insert(0,str(EMMC))
import backup_emmc as backup


class BackupAcquireTests(unittest.TestCase):
    def test_capacity_ioctl_uses_python_word_size(self):
        for word_size,expected in ((4,0x80041272),(8,0x80081272)):
            spec=importlib.util.spec_from_file_location('block_device_test',EMMC/'block_device.py')
            module=importlib.util.module_from_spec(spec)
            with patch.object(struct,'calcsize',return_value=word_size):spec.loader.exec_module(module)
            self.assertEqual(module.BLKGETSIZE64,expected)

    def test_acquire_and_independent_readback_both_abis(self):
        for request in (0x80041272,0x80081272):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'source';dest=root/'mmcblk0.img'
                content=bytes(range(256))*5;source.write_bytes(content);events=[]
                def ioctl(fd,number,buffer):
                    self.assertEqual(number,request);self.assertEqual(buffer,bytes(8))
                    return struct.pack('Q',len(content))
                with patch.object(backup,'BLKGETSIZE64',request),patch.object(backup,'CHUNK',127), \
                     patch.object(backup.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                     patch.object(backup.fcntl,'ioctl',side_effect=ioctl):
                    result=backup.acquire(str(source),dest,len(content),lambda *a:events.append(a))
                self.assertEqual(dest.read_bytes(),content)
                self.assertEqual(result['sha256'],hashlib.sha256(content).hexdigest())
                self.assertTrue(result['readback_verified'])
                self.assertIn(('copy',len(content),len(content)),events)
                self.assertIn(('verify',len(content),len(content)),events)
                self.assertFalse(dest.with_suffix('.img.partial').exists())

    def test_failed_capacity_query_creates_no_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.write_bytes(bytes(64));dest=root/'out.img'
            with patch.object(backup.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                 patch.object(backup.fcntl,'ioctl',side_effect=OSError(errno.EINVAL,'Invalid argument')):
                with self.assertRaises(OSError) as error:backup.acquire(str(source),dest,64)
            self.assertEqual(error.exception.errno,errno.EINVAL)
            self.assertEqual(error.exception.filename,str(source))
            self.assertIn('ioctl=',str(error.exception))
            self.assertFalse(dest.exists());self.assertFalse(dest.with_suffix('.img.partial').exists())

    def test_failed_readback_never_promotes_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.write_bytes(bytes(64));dest=root/'out.img'
            with patch.object(backup.os,'fstat',return_value=SimpleNamespace(st_mode=stat.S_IFBLK)), \
                 patch.object(backup.fcntl,'ioctl',return_value=struct.pack('Q',64)), \
                 patch.object(backup,'hash_file',return_value='wrong'):
                with self.assertRaisesRegex(RuntimeError,'readback'):backup.acquire(str(source),dest,64)
            self.assertFalse(dest.exists());self.assertTrue(dest.with_suffix('.img.partial').exists())
