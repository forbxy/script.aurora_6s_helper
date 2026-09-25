import contextlib
import importlib.util
import json
from pathlib import Path
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
from progress import ByteProgress
from live_snapshot import copy_live_storage
import worker
import operations as op
from test_emmc import report

class ForegroundTests(unittest.TestCase):
    def test_accounting_includes_each_pass_without_premature_100(self):
        p=ByteProgress();p.plan({'copy':100,'verify':100})
        p.advance('copy',100,100);self.assertEqual(p.fields()['percent'],50)
        p.advance('verify',50,100);self.assertEqual(p.fields()['processed_bytes'],150)
        p.advance('verify',100,100);self.assertEqual(p.fields()['percent'],99)
        self.assertEqual(p.fields(True)['percent'],100)
    def test_live_copy_excludes_backups_and_keeps_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';dest=Path(tmp)/'dest';source.mkdir();dest.mkdir()
            (source/'settings').write_bytes(b'config'*1024)
            os.link(source/'settings',source/'hardlink')
            (source/'link').symlink_to('settings')
            (source/'aurora-emmc-backups').mkdir();(source/'aurora-emmc-backups/large.img').write_bytes(b'not migrated')
            events=[]
            copy_live_storage(source,dest,'-aH',['/aurora-emmc-backups/'],None,lambda *a,**kw:events.append(kw))
            self.assertEqual((dest/'settings').read_bytes(),(source/'settings').read_bytes())
            self.assertFalse((dest/'aurora-emmc-backups').exists())
            self.assertTrue((dest/'link').is_symlink())
            self.assertEqual((dest/'settings').stat().st_ino,(dest/'hardlink').stat().st_ino)
            self.assertEqual(events[-1]['progress_done'],events[-1]['progress_total'])
    def test_submit_prepares_only_does_not_start_daemon_or_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);realopen=open
            def opened(path,*a,**kw):return realopen(root/'lock',*a,**kw)
            with patch.object(op,'assess',return_value=report()),patch.object(op,'BASE',root),patch.object(worker,'private_external',side_effect=lambda p:Path(p)),patch.object(worker,'status',return_value={'phase':'none'}),patch('worker.open',side_effect=opened,create=True),patch.object(worker.shutil,'copytree',side_effect=lambda a,b,**kw:Path(b).mkdir()),patch.object(op,'run') as run,patch.object(worker,'execute') as execute:
                result=worker.submit('backup')
                run.assert_not_called();execute.assert_not_called()
                self.assertTrue(Path(result['job'],'request.json').is_file())
                self.assertIn('runtime',result)
    def test_safe_cancel_prevents_backup_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=report()
            (root/'request.json').write_text(json.dumps(dict(action='backup',reset_accepted=False,risk_accepted=False,report=r,source_backup=None,boot_id='boot')))
            (root/'status.json').write_text(json.dumps(dict(phase='queued',device_writes_started=False)))
            (root/'cancel').touch();realopen=open
            def opened(path,*a,**kw):return realopen(root/Path(path).name,*a,**kw)
            with patch.object(worker,'private_external',return_value=root),patch.object(worker,'read',return_value='boot'),patch('worker.open',side_effect=opened,create=True),patch.object(op,'assess',return_value=r),patch.object(op,'same_device'),patch.object(op,'full_backup') as backup:
                worker.execute(root);backup.assert_not_called()
            self.assertEqual(json.loads((root/'status.json').read_text())['phase'],'cancelled')
    def test_frontend_waits_for_child_and_updates_modal_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp);(job/'status.json').write_text(json.dumps({'phase':'queued','message':'准备'}))
            fake=job/'worker.py'
            fake.write_text("import json,sys,time\nfrom pathlib import Path\np=Path(sys.argv[2])/'status.json'\np.write_text(json.dumps({'phase':'verifying','message':'校验','percent':50,'processed_bytes':100,'total_bytes':200}))\ntime.sleep(0.2)\np.write_text(json.dumps({'phase':'complete','message':'完成','percent':100}))\n",encoding='utf-8')
            updates=[]
            class Dialog:
                def create(self,*a):pass
                def update(self,*a):updates.append(a)
                def iscanceled(self):return False
                def close(self):pass
            class Monitor:
                def waitForAbort(self,n):time.sleep(0.01);return False
            spec=importlib.util.spec_from_file_location('ui_foreground',Path(__file__).resolve().parents[1]/'resources/lib/emmc_ui.py');ui=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,{'xbmc':types.SimpleNamespace(Monitor=Monitor),'xbmcgui':types.SimpleNamespace(DialogProgress=Dialog)}):spec.loader.exec_module(ui)
            with patch.object(ui,'show_status') as show:ui.run_foreground(dict(job=str(job),runtime=str(fake)),'test');show.assert_called_once()
            self.assertTrue(any(u[0]==50 for u in updates))
            self.assertEqual(json.loads((job/'status.json').read_text())['phase'],'complete')
