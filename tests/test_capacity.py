"""Capacity arithmetic, legacy compatibility and UI/worker hand-off."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch
from test_emmc import report
import policy
import operations as op
import worker

GIB=1024**3

class CapacityTests(unittest.TestCase):
    def test_every_offer_has_exact_android_capacity_and_roundtrips(self):
        for model,capacity in [('A4111',62537072640),('A4112',62545461248)]:
            r=report(model,capacity)
            original=copy.deepcopy(r)
            for choice in policy.capacity_choices(r,3*GIB):
                rows=policy.install_layout(r,choice['android_gib'])
                self.assertEqual(rows[28]['size'],GIB)
                self.assertEqual(rows[29]['size'],choice['ce_storage_bytes'])
                self.assertEqual(rows[30]['size'],choice['android_gib']*GIB)
                self.assertEqual(rows[-1]['offset']+rows[-1]['size'],capacity)
                for prev,nxt in zip(rows[27:],rows[28:]):
                    self.assertEqual(nxt['offset'],prev['offset']+prev['size']+8*1024**2)
                self.assertTrue(all(x['offset']%512==x['size']%512==0 for x in rows))
                live=copy.deepcopy(r);live['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in rows]
                kind,stock,recognized=policy.layouts(live)
                self.assertEqual(kind,'dual');self.assertEqual(recognized,rows)
                self.assertEqual([(x['name'],x['offset'],x['size']) for x in stock],[(x['name'],x['offset'],x['size']) for x in r['mpt']['partitions']])
            self.assertEqual(r,original)
    def test_legacy_20_gib_is_still_recognized(self):
        r=report();rows=policy.layouts(r)[2]
        self.assertEqual(rows[29]['size'],20*GIB)
        r['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in rows]
        self.assertEqual(policy.layouts(r)[2],rows)
    def test_invalid_selection_or_insufficient_capacity(self):
        for n in [True,None,7,0,-1,8.5,'16',100000]:
            with self.subTest(n=n),self.assertRaises(ValueError):policy.install_layout(report(),n)
        with self.assertRaises(ValueError):policy.capacity_choices(report(),60*GIB)
    def test_usage_filters_choices_and_leaves_headroom(self):
        r=report();low=policy.capacity_choices(r,0);high=policy.capacity_choices(r,30*GIB)
        self.assertLess(len(high),len(low))
        for choice in high:self.assertGreaterEqual(policy.ce_data_limit(choice['ce_storage_bytes']),30*GIB)
        last=high[-1]['android_gib']
        self.assertLess(policy.ce_data_limit(policy.install_layout(r,last+1)[29]['size']),30*GIB)
    def test_damaged_variable_layout_rejected(self):
        for index,key,value in [(28,'size',2*GIB),(29,'offset',1),(29,'size',123),(30,'offset',1),(30,'flags',2),(30,'size',8*GIB),(29,'name','userdata')]:
            r=report();rows=policy.install_layout(r,16);rows[index][key]=value
            r['mpt']['partitions']=[dict(x,sysfs_matches=True) for x in rows]
            with self.subTest(index=index,key=key),self.assertRaises(ValueError):policy.layouts(r)
    def test_selection_pinned_in_private_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=report();r['android_gib']=24;realopen=open
            with patch.object(op,'assess',return_value=r) as assess,patch.object(op,'BASE',root),patch.object(worker,'private_external',side_effect=lambda p:Path(p)),patch.object(worker,'status',return_value={'phase':'none'}),patch('worker.open',side_effect=lambda path,*a,**kw:realopen(root/'lock',*a,**kw),create=True),patch.object(worker.shutil,'copytree',side_effect=lambda a,b,**kw:Path(b).mkdir()):
                result=worker.submit('install',reset=True,risk=True,android_gib=24)
                assess.assert_called_once_with('install',None,24)
            request=json.loads(Path(result['job'],'request.json').read_text())
            self.assertEqual(request['android_gib'],24)
    def test_other_actions_refuse_capacity_option_before_probe(self):
        for action in ['backup','restore','remove',None]:
            with patch.object(op,'probe') as probe,self.assertRaises(ValueError):op.assess(action,android_gib=16)
            probe.assert_not_called()

class CapacityUITests(unittest.TestCase):
    def run_ui(self,selection):
        confirmations=[]
        class Dialog:
            def select(self,title,items,**kw):
                if title=='eMMC 双系统与备份':return 0
                self_test.assertEqual(kw['preselect'],1)
                return selection
            def yesno(self,title,message,**kw):confirmations.append(message);return True
        class Progress:
            def create(self,*a):pass
            def close(self):pass
        self_test=self
        spec=importlib.util.spec_from_file_location('capacity_ui',Path(__file__).resolve().parents[1]/'resources/lib/emmc_ui.py');ui=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'xbmc':types.ModuleType('xbmc'),'xbmcgui':types.SimpleNamespace(Dialog=Dialog,DialogProgress=Progress)}):spec.loader.exec_module(ui)
        r=report();r.update(android_gib=16,capacity_choices=[dict(android_gib=8,ce_storage_bytes=46*GIB),dict(android_gib=16,ce_storage_bytes=38*GIB)])
        with patch.object(ui,'call',side_effect=[r,{'job':'test'}]) as call,patch.object(ui,'run_foreground') as run:
            ui.main()
        return call,run,confirmations
    def test_selected_capacity_sent_and_confirmed(self):
        call,run,messages=self.run_ui(0)
        self.assertEqual(call.call_args.args,('submit','install','--reset-android','--accept-risk','--android-gib','8'))
        self.assertIn('Android 用户数据 8 GiB',messages[0]);self.assertIn('46.00 GiB',messages[0]);run.assert_called_once()
    def test_cancel_selection_never_submits(self):
        call,run,messages=self.run_ui(-1)
        self.assertEqual(call.call_count,1);run.assert_not_called();self.assertFalse(messages)
