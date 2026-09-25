import io
from pathlib import Path
import struct
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'resources/emmc'))

from android_identity import (metadata_geometry, parse_active_slot,
                              selected_vendor_extents, validate_vendor_filesystem,
                              vendor_extents)


class AndroidSlotTests(unittest.TestCase):
    def setUp(self):
        self.raw = (Path(__file__).parent/'fixtures/vendor-ab-metadata.bin').read_bytes()
        self.capacity = 1800*1024**2

    def test_reported_geometry_and_vendor_lengths(self):
        self.assertEqual(metadata_geometry(self.raw), (65536, 3, 4096))
        a = selected_vendor_extents(self.raw, self.capacity, parse_active_slot('active_slot=_a\n'))
        b = selected_vendor_extents(self.raw, self.capacity, parse_active_slot('active_slot=_b\n'))
        self.assertEqual(len(a), 2)
        self.assertEqual(len(b), 3)
        self.assertEqual(sum(n*512 for n,_ in a), 24788*4096)
        self.assertEqual(sum(n*512 for n,_ in b), 109654016)
        self.assertEqual(b[:2], a)
        self.assertEqual(b[2], (15864, 2570568))
        for slot in (0,1):
            self.assertEqual(vendor_extents(self.raw,self.capacity,slot),
                             vendor_extents(self.raw,self.capacity,slot,backup=True))

    def test_slot_b_corrupt_primary_uses_b_backup_only(self):
        raw = bytearray(self.raw);raw[77824+12] ^= 1
        b = selected_vendor_extents(raw,self.capacity,1)
        self.assertEqual(len(b),3)
        raw[274432+12] ^= 1
        with self.assertRaisesRegex(ValueError,'主备元数据'):
            selected_vendor_extents(raw,self.capacity,1)
        self.assertEqual(len(selected_vendor_extents(raw,self.capacity,0)),2)

    def test_invalid_active_slot_does_not_guess(self):
        for value in ('', '_b', 'active_slot=_c', 'active_slot=_a\nactive_slot=_b', 'active_slot='):
            with self.assertRaises(ValueError):parse_active_slot(value)

    def test_backup_geometry_and_invalid_bounds(self):
        raw=bytearray(self.raw);raw[4104]^=1
        self.assertEqual(metadata_geometry(raw),(65536,3,4096))
        self.assertEqual(len(selected_vendor_extents(raw,self.capacity,1)),3)
        with self.assertRaises(ValueError):selected_vendor_extents(self.raw,self.capacity,2)

    def test_ext4_size_checked_without_mount_or_growth(self):
        raw=bytearray(2048)
        struct.pack_into('<H',raw,1024+56,0xef53)
        struct.pack_into('<I',raw,1024+24,2)
        struct.pack_into('<I',raw,1024+4,26328)
        with self.assertRaisesRegex(ValueError,'拒绝扩大映射'):
            validate_vendor_filesystem(io.BytesIO(raw),[(198304,0)])
        self.assertEqual(validate_vendor_filesystem(io.BytesIO(raw),[(214168,0)]),26328*4096)
        struct.pack_into('<I',raw,1024+96,0x80)
        struct.pack_into('<I',raw,1024+336,1)
        with self.assertRaises(ValueError):validate_vendor_filesystem(io.BytesIO(raw),[(214168,0)])
        with self.assertRaises(ValueError):validate_vendor_filesystem(io.BytesIO(bytes(2048)),[(214168,0)])
