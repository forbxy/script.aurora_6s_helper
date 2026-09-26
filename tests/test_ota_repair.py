"""Synthetic live layouts; no installation records or device access required."""
import copy,contextlib,hashlib,json,os,struct,sys,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'resources/emmc'))
import ota_repair as repair
import ota_repair_core as core
import operations as op
import policy,worker
from test_emmc import report
from prepare_boot_environment import BOOTCMD,scan_through


def env():
 return dict(bootcmd=BOOTCMD,bootfromemmc='run cfgloademmc',bootfromnand='0',active_slot='normal',storeboot='get_valid_slot; imgread kernel ${boot_part}',cfgloademmc=scan_through(31))

def misc(slot='_b'):
 b=bytearray(65536);b[2048:2052]=slot.encode()+b'\0\0';struct.pack_into('<I',b,2052,0x42414342);b[2056]=1;b[2057]=2
 b[2060],b[2062]=(7,0x97) if slot=='_b' else (0x97,7)
 struct.pack_into('<I',b,2076,zlib.crc32(b[2048:2076]));return bytes(b)

def reader(r,size):
 fat=bytearray(4096);fat[510:512]=b'\x55\xaa';fat[82:90]=b'FAT32   ';fat[71:82]=b'CE_AURORA  '
 struct.pack_into('<H',fat,11,512);struct.pack_into('<I',fat,32,(1024**3-4096)//512)
 sb=bytearray(1024);sb[56:58]=b'\x53\xef';sb[120:136]=b'CE_AURORA_DATA'.ljust(16,b'\0');struct.pack_into('<I',sb,24,2);struct.pack_into('<I',sb,4,size//4096)
 start=r['mpt']['partitions'][-1]['offset'];mo=next(p['offset'] for p in r['mpt']['partitions'] if p['name']=='misc')
 mapping={(start,4096):bytes(fat),(start+1024**3+8*1024**2+1024,1024):bytes(sb),(mo,65536):misc()}
 return lambda off,n:mapping[off,n]

class RecoveryTests(unittest.TestCase):
 def test_live_inference_different_models_and_sizes(self):
  for model in ('A4111','A4112'):
   for cap in (62537072640,128*1024**3):
    for size in (9*1024**3,45923434496):
     r=report(model,cap);target,_=core.discover_layout(r,reader(r,size))
     self.assertEqual(target[-2]['size'],size)
     self.assertEqual(target[-1]['offset']+target[-1]['size'],cap)
 def test_quick_preview_retains_normal_and_has_no_writes(self):
  r=report();before=env()
  with patch.object(repair,'check_env_device'),patch.object(repair,'read_at',side_effect=reader(r,45923434496)),patch.object(repair,'env_read',return_value=before):
   repair.assess(r)
  self.assertEqual(before['active_slot'],'normal');self.assertEqual(r['ota_repair']['environment_changes'],{})
  self.assertEqual(r['ota_repair']['partitions'][-1]['size'],12*1024**3)
 def test_ng_rejected_before_mapping(self):
  r=report();r['os_release'].update(DISTRO_DEVICE='Amlogic-ng',VERSION_ID='21.3')
  with patch.object(repair,'check_env_device') as check:
   with self.assertRaisesRegex(ValueError,'NO'):repair.assess(r)
   check.assert_not_called()
 def test_healthy_dual_layout_is_not_a_repair_candidate(self):
  r=report();r['mpt']['partitions']=[dict(p,sysfs_matches=True) for p in policy.layouts(r)[2]]
  with self.assertRaisesRegex(ValueError,'original 29'):core.discover_layout(r,lambda *_:self.fail('No header read on healthy layout'))
 def test_misc_crc_and_pending_merge_rejected(self):
  for suffix in ('_a','_b'):self.assertEqual(core.select_slot(misc(suffix))['selected'],suffix)
  bad=bytearray(misc());bad[2060]^=1
  with self.assertRaisesRegex(ValueError,'CRC'):core.select_slot(bad)
  bad=bytearray(misc());bad[32768:32774]=b'\x02\xb0\x0a\x74\x56\x03'
  with self.assertRaisesRegex(ValueError,'merge'):core.select_slot(bad)
 def test_wrong_geometry_rejected(self):
  r=report()
  with self.assertRaises(ValueError):core.discover_layout(r,reader(r,128*1024**3))
  with self.assertRaises(ValueError):core.discover_layout(r,lambda off,n:b'\0'*n)
 def test_old_or_environment_write_plan_rejected(self):
  for plan in ({'schema':1},{'schema':2,'repair_mode':'layout-only','environment_changes':{'active_slot':'_b'}}):
   with self.assertRaises(ValueError):repair.validate_plan_scope(plan)
 def test_confirmation_does_not_require_android_reset(self):
  policy.confirmation('repair',False,True)
  with patch.object(op,'assess') as assess:
   with self.assertRaises(ValueError):worker.submit('repair',risk=False)
   assess.assert_not_called()
 def test_preserved_hash_covers_misc_beyond_header(self):
  r={'mpt':{'partitions':[dict(name='env',offset=0,size=65536),dict(name='misc',offset=65536,size=131072)]}}
  plan={'preserved_sha256':{p['name']:core.sha(b'Z'*p['size']) for p in r['mpt']['partitions']}}
  with tempfile.TemporaryFile() as f:
   f.write(b'Z'*196608);f.flush();repair.verify_preserved(f.fileno(),r,plan)
   os.pwrite(f.fileno(),b'X',65536+70000)
   with self.assertRaisesRegex(ValueError,'misc'):repair.verify_preserved(f.fileno(),r,plan)
 def test_worker_repair_never_calls_install_remove_or_backup(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);job=root/'job';job.mkdir();r=report()
   (job/'request.json').write_text(json.dumps(dict(action='repair',reset_accepted=False,risk_accepted=True,report=r,source_backup=None,boot_id='testboot')))
   (job/'status.json').write_text(json.dumps(dict(phase='queued',boot_id='testboot',device_writes_started=False)))
   realopen=open
   def opened(path,*args,**kwargs):
    if str(path).startswith('/run/aurora-emmc-'):return realopen(root/Path(path).name,*args,**kwargs)
    return realopen(path,*args,**kwargs)
   events=[]
   def recovery(folder,live,update):
    events.append(folder);update('repair-writing','test',device_writes_started=True)
   with contextlib.ExitStack() as stack:
    stack.enter_context(patch.object(worker,'private_external',return_value=job));stack.enter_context(patch.object(worker,'read',return_value='testboot'));stack.enter_context(patch('worker.open',side_effect=opened,create=True))
    stack.enter_context(patch.object(op,'assess',return_value=r));stack.enter_context(patch.object(op,'same_device'))
    for name in ('full_backup','zero_userdata','commit_metadata','change_environment','environment'):
     stack.enter_context(patch.object(op,name,side_effect=AssertionError('Unexpected '+name)))
    stack.enter_context(patch.object(repair,'execute',side_effect=recovery))
    worker.execute(job)
   self.assertEqual(events,[job]);state=json.loads((job/'status.json').read_text());self.assertEqual(state['phase'],'complete');self.assertTrue(state['reboot_required'])
 def test_fsck_cancellation_cleans_child(self):
  class Cancel(Exception):pass
  from unittest.mock import MagicMock
  process=MagicMock();process.__enter__.return_value=process;process.poll.return_value=None
  with patch.object(repair.subprocess,'Popen',return_value=process):
   with self.assertRaises(Cancel):repair.run_fsck(['never-run'],lambda *a,**k:(_ for _ in ()).throw(Cancel()),'ce_storage')
  process.kill.assert_called_once();process.communicate.assert_called_once()
if __name__=='__main__':unittest.main()
