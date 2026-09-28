"""Boot migration uses temporary ordinary directories; never touches block devices."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import boot_files as bf
import prepare_boot as pb
import stage_system as ss


class BootFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'flash';self.source.mkdir()
        self.out=self.root/'stage';self.out.mkdir()
        for name in bf.BOOT_REQUIRED:(self.source/name).write_bytes(name.encode())
        (self.source/'config.ini').write_bytes(b'# custom settings\r\nfoo=bar\r\n')
        for directory in ('device_trees/sub','custom/empty','.hidden','custom/device_trees'):
            (self.source/directory).mkdir(parents=True,exist_ok=True)
        for name in ('device_trees/sub/skip.dtb','custom/driver.ko','custom/device_trees/keep','extra.ini','aml_autoscript','custom/aml_autoscript','.hidden/file','中文配置.txt'):
            (self.source/name).write_bytes(name.encode()*13)
        self.stock=self.root/'Generic_cfgload';self.stock.write_bytes(b'checked stock script')
        for module in (pb,ss):
            for attr,value in [('STOCK_CFGLOAD',self.stock),('validate_stock_cfgload',lambda raw:raw)]:
                p=patch.object(module,attr,value);p.start();self.addCleanup(p.stop)
        p=patch.object(ss,'private_external',lambda path:Path(path));p.start();self.addCleanup(p.stop)

    def stage(self):
        entries,dirs=pb.stage_files(self.source,self.out)
        manifest=dict(schema=3,boot_strategy='stock-cfgload-config-rootopt',
                      boot_files=entries,boot_directories=dirs,boot_excludes=bf.BOOT_EXCLUDES)
        (self.out/'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_recursive_copy_excludes_root_multiboot_and_device_trees(self):
        before={p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        manifest=self.stage();ss.validate_boot(self.out,self.source)
        boot=self.out/'boot'
        self.assertFalse((boot/'device_trees').exists())
        self.assertFalse((boot/'aml_autoscript').exists())
        self.assertTrue((boot/'custom/empty').is_dir())
        for name,data in before.items():
            if name.startswith('device_trees/') or name in ('config.ini','cfgload','aml_autoscript'):continue
            self.assertEqual((boot/name).read_bytes(),data)
        self.assertEqual((boot/'cfgload').read_bytes(),self.stock.read_bytes())
        self.assertEqual((boot/'config.ini').read_bytes(),pb.configure_rootopt(before['config.ini']))
        self.assertEqual(before,{p.relative_to(self.source).as_posix():p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        dest=self.root/'copied';shutil.copytree(boot,dest)
        bf.verify_boot_tree(dest,manifest)
        self.assertEqual(sum(r['bytes'] for r in manifest['boot_files']),sum(p.stat().st_size for p in boot.rglob('*') if p.is_file()))

    def test_source_addition_after_staging_refused(self):
        self.stage();(self.source/'custom/new').write_bytes(b'new')
        with self.assertRaises(ValueError):ss.validate_boot(self.out,self.source)

    def test_excluded_tree_changes_do_not_invalidate_snapshot(self):
        self.stage();(self.source/'device_trees/new').write_bytes(b'new')
        (self.source/'aml_autoscript').write_bytes(b'updated multiboot script')
        ss.validate_boot(self.out,self.source)

    def test_staged_extra_file_and_missing_empty_directory_refused(self):
        m=self.stage();extra=self.out/'boot/extra';extra.write_bytes(b'x')
        with self.assertRaises(ValueError):ss.validate_boot(self.out,self.source)
        extra.unlink();(self.out/'boot/custom/empty').rmdir()
        with self.assertRaises(ValueError):bf.verify_boot_tree(self.out/'boot',m)

    def test_same_size_corruption_refused(self):
        self.stage();p=self.out/'boot/custom/driver.ko';p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaises(ValueError):ss.validate_boot(self.out,self.source)

    def test_manifest_traversal_absolute_and_excluded_paths_refused(self):
        original=self.stage()
        for path in ('../secret','/tmp/secret','custom/../secret','custom//secret','device_trees/x','aml_autoscript','custom\\x'):
            m=json.loads(json.dumps(original));m['boot_files'][-1]['file']=path
            with self.subTest(path=path),self.assertRaises(ValueError):bf.boot_manifest_inventory(m)

    def test_symlink_and_special_files_refused(self):
        import os
        link=self.source/'custom/link';link.symlink_to(self.stock)
        with self.assertRaises(ValueError):pb.source_inventory(self.source)
        link.unlink();os.mkfifo(link)
        with self.assertRaises(ValueError):pb.source_inventory(self.source)

    def test_capacity_includes_nested_files_before_staging(self):
        with (self.source/'custom/huge').open('wb') as f:f.truncate(901*1024**2)
        with self.assertRaisesRegex(ValueError,'预留容量'):self.stage()
        self.assertFalse((self.out/'boot').exists())

    def test_capacity_counts_empty_directories_and_tiny_file_allocation(self):
        with self.assertRaisesRegex(ValueError,'预留容量'):
            bf.check_boot_capacity({str(i):1 for i in range(15000)},[])
        with self.assertRaisesRegex(ValueError,'预留容量'):
            bf.check_boot_capacity({},[str(i) for i in range(15000)])

    def test_existing_rootopt_and_conflicting_config(self):
        config=self.source/'config.ini';config.write_bytes(pb.configure_rootopt(config.read_bytes()))
        before=config.read_bytes();self.stage();self.assertEqual((self.out/'boot/config.ini').read_bytes(),before)
        config.write_text('rootopt=wrong\n')
        with self.assertRaisesRegex(ValueError,'conflicts'):pb.source_inventory(self.source)

    def test_legacy_manifest_still_validates(self):
        for path in list(self.source.iterdir()):
            if path.name not in bf.BOOT_REQUIRED:
                if path.is_dir():shutil.rmtree(path)
                else:path.unlink()
        m=self.stage();m['schema']=2;del m['boot_directories'];del m['boot_excludes']
        (self.out/'manifest.json').write_text(json.dumps(m))
        ss.validate_boot(self.out,self.source)

if __name__=='__main__':unittest.main()
