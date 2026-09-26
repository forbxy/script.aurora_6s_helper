import hashlib,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'resources/lib'))
import device,repair

class NoHs400Tests(unittest.TestCase):
 def test_payload_checksums(self):repair.verify_payload()
 @unittest.skipUnless(shutil.which('fdtget'),'dtc tools required')
 def test_default_dtb_properties_match_profile_ids(self):
  for key,ident in device.PROFILE_IDS.items():
   if not key.startswith('no/'):continue
   path=str(ROOT/'resources/payload'/key/'dtb.img')
   def get(node,prop,typ='s'):return subprocess.check_output(['fdtget','-t',typ,path,node,prop],text=True).strip()
   self.assertTrue(ident.endswith('_hs400'));self.assertEqual(get('/','coreelec-dt-id'),ident)
   self.assertEqual(get('/soc/mmc@fe08c000','tx_delay','i'),'16')
   self.assertEqual(get('/soc/mmc@fe08c000','mmc-hs400-1_8v'),'')
   self.assertEqual(get('/soc/mmc@fe08c000','max-frequency','i'),'200000000')
 def test_lighting_accepts_old_and_new_no_ids_without_android_read(self):
  with tempfile.TemporaryDirectory() as tmp:
   release=Path(tmp)/'os-release';release.write_text('ID=coreelec\nCOREELEC_DEVICE=Amlogic-no\nVERSION_ID=22.0\n')
   for key,ident in device.PROFILE_IDS.items():
    if not key.startswith('no/'):continue
    for current in (ident,ident.removesuffix('_hs400')):
     with patch.object(device,'RELEASE',release),patch.object(device,'android_board',side_effect=AssertionError('No Android read for lighting')),patch.object(device,'text_property',side_effect=lambda name:{'compatible':'amlogic,sc2','model':'test','coreelec-dt-id':current}[name]):
      result=device.detect(check_kernel=False,runtime_profile=True)
      self.assertEqual(result['payload'],key)
      self.assertEqual(result['expected_dt_id'],ident)
