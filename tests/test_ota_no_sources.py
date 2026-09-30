"""NO's Android read-only mounts must neither block nor weaken OTA recovery."""
import contextlib,sys,types,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import ota_resume as resume

class SourceTests(unittest.TestCase):
    @contextlib.contextmanager
    def machine(self,name='dynpart-system_a',table='0 16 linear 179:28 8',mount='/android/system',ro=True,other=False,child=False):
        rows=[{'name':'super','size':512*100},{'name':'misc','size':512*16}]
        dm=Path('/sys/block/mmcblk0/super/holders/dm-0')
        values={'/sys/block/mmcblk0/super/dev':'179:28','/sys/block/mmcblk0/misc/dev':'179:11',str(dm/'dm/name'):name,str(dm/'dev'):'254:0'}
        def glob(p,pattern):
            if str(p)=='/sys/block/mmcblk0/super/holders':return iter([dm])
            if str(p)=='/sys/block/mmcblk0/misc/holders' and other:return iter([dm])
            if p==dm/'holders' and child:return iter([Path('dm-1')])
            return iter([])
        with contextlib.ExitStack() as st:
            st.enter_context(patch.object(resume.op,'target_idle'))
            st.enter_context(patch.object(resume.op,'read',side_effect=lambda p:values.get(str(p),'')))
            st.enter_context(patch.object(Path,'glob',glob))
            st.enter_context(patch.object(resume.op,'mountinfo',return_value=[{'dev':'254:0','mountpoint':mount}]))
            st.enter_context(patch.object(resume.os,'statvfs',return_value=types.SimpleNamespace(f_flag=resume.os.ST_RDONLY)))
            st.enter_context(patch.object(resume.op,'run',return_value=table))
            st.enter_context(patch.object(resume.layout,'require_readonly',side_effect=None if ro else ValueError('Mapping not read-only')))
            yield {'mpt':{'partitions':rows}}

    def test_normal_no_readonly_library_mount_is_allowed(self):
        with self.machine() as r:resume.sources_idle(r)
        with self.machine(name='dynpart-system_ext_b',mount='/android/system_ext',table='0 8 linear 179:28 8\n8 8 linear 179:28 24') as r:resume.sources_idle(r)

    def test_writable_unknown_or_indirect_mapping_refused(self):
        for args in ({'ro':False},{'name':'unrelated-map'},{'child':True},{'other':True},{'mount':'/storage/media'}):
            with self.subTest(args=args),self.machine(**args) as r:
                with self.assertRaises(ValueError):resume.sources_idle(r)

    def test_wrong_disk_target_and_out_of_bounds_refused(self):
        for table in ('0 16 linear 179:0 8','0 16 snapshot 179:28 8','0 16 linear 179:28 99','8 16 linear 179:28 8','0 0 linear 179:28 8',''):
            with self.subTest(table=table),self.machine(table=table) as r:
                with self.assertRaises(ValueError):resume.sources_idle(r)

    def test_direct_partition_mount_refused(self):
        with self.machine() as r,patch.object(resume.op,'mountinfo',return_value=[{'dev':'179:28','mountpoint':'/android/super'}]):
            with self.assertRaises(ValueError):resume.sources_idle(r)

    def test_metadata_mapping_uses_selected_branch_backend(self):
        row={'name':'metadata','offset':675282944,'size':16*1024**2,'sysfs_matches':True}
        r={'mpt':{'partitions':[row]}}
        for branch in ('NG','NO'):
            backend=Mock();backend.region.return_value=contextlib.nullcontext(Path('/dev/test-'+branch))
            with self.subTest(branch=branch),patch.object(resume.op,'backend_for',return_value=backend),patch.object(resume.op,'region_dm',side_effect=AssertionError('must not bypass backend')),patch('policy.layouts'),patch.object(resume.layout,'require_readonly'),patch.object(resume.layout,'run'):
                with resume.metadata_mount(r):pass
                backend.region.assert_called_once_with(resume.op,row['offset'],row['size'],readonly=True)

if __name__=='__main__':unittest.main()
