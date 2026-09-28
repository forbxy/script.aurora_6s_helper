import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import health


class HealthTests(unittest.TestCase):
    def test_hex_formats_and_invalid_values(self):
        for value in ('01','0x01','0X01'):
            self.assertEqual(health.byte_value(value),1)
        self.assertEqual(health.byte_value('0a'),10)
        for value in ('','oops','100','-1','1 2'):
            self.assertIsNone(health.byte_value(value))

    def test_lifetime_boundaries(self):
        self.assertIn('0%–10%',health.lifetime(1))
        self.assertIn('90%–100%',health.lifetime(10))
        self.assertIn('超过',health.lifetime(11))
        self.assertIn('未定义',health.lifetime(0))
        self.assertIn('保留',health.lifetime(12))
        self.assertIn('无法读取',health.lifetime(None))

    def test_incomplete_and_alarm_states_not_reported_healthy(self):
        self.assertIn('未见',health.summary(1,1,1))
        for a,b,e in [(0,1,1),(None,1,1),(1,255,1),(1,1,None),(1,1,0),(1,1,255)]:
            self.assertIn('不完整',health.summary(a,b,e))
        self.assertIn('告警',health.summary(11,0,0))
        self.assertIn('告警',health.summary(None,None,3))
        self.assertIn('预警',health.summary(10,1,1))
        self.assertIn('预警',health.summary(None,None,2))

    def test_discovery_only_mmc_whole_devices_and_missing_attrs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,kind in [('mmcblk0','SD'),('mmcblk1','MMC'),('mmcblk1boot0','MMC'),('mmcblk1rpmb','MMC'),('mmcblk1p1','MMC')]:
                p=root/name/'device';p.mkdir(parents=True);(p/'type').write_text(kind)
            p=root/'mmcblk1';(p/'size').write_text('2097152')
            (p/'device/name').write_text('test')
            (p/'device/life_time').write_text('0x01 0x0a')
            for raw in ('01','0x01'):
                (p/'device/pre_eol_info').write_text(raw)
                result=health.probe(root);self.assertEqual(len(result['devices']),1)
                d=result['devices'][0];self.assertEqual(d['device'],'/dev/mmcblk1')
                self.assertEqual(d['capacity_bytes'],1024**3);self.assertEqual(d['pre_eol'],1)
                self.assertIn('90%–100%',result['text'])
            (p/'device/life_time').write_text('garbage')
            (p/'device/pre_eol_info').unlink()
            self.assertIn('不完整',health.probe(root)['text'])
            self.assertEqual(health.probe(root/'missing')['devices'],[])

if __name__=='__main__':unittest.main()
