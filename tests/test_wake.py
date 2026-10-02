import importlib.util,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'resources/wake'))
sys.path.insert(0,str(ROOT/'resources/lib'))
import common,wake_install
spec=importlib.util.spec_from_file_location('wake_runtime',ROOT/'resources/wake/runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)

class WakeTests(unittest.TestCase):
 def test_startup_waits_for_stable_controller_and_pid(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(runtime,'REPAIR_LOCK',Path(temp)/'lock'),patch.object(runtime,'command',return_value='active'),patch.object(runtime,'startup_controller',side_effect=[None,None,('1','mac'),('2','mac'),('2','mac'),('2','mac'),('2','mac')]),patch.object(runtime.time,'sleep') as sleep,patch.object(runtime,'initialize') as initialize,patch.object(runtime,'event'):
   self.assertTrue(runtime.initialize_when_ready(lambda:False))
   self.assertEqual(sleep.call_count,5)
   initialize.assert_called_once()
 def test_startup_timeout_leaves_initialization_alone(self):
  with patch.object(runtime,'command',return_value='active'),patch.object(runtime,'startup_controller',return_value=None),patch.object(runtime.time,'monotonic',side_effect=[0,0,91]),patch.object(runtime.time,'sleep'),patch.object(runtime,'initialize') as initialize,patch.object(runtime,'event'):
   with self.assertRaisesRegex(RuntimeError,'未改动控制器'):runtime.initialize_when_ready(lambda:False)
   initialize.assert_not_called()
 def test_config_overlay_does_not_write_base_file(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);base=root/'config';wake=root/'rtl8852bs_config.wake'
   base.write_bytes(b'base');wake.write_bytes(b'wake')
   with patch.object(runtime,'BASE',root),patch.object(runtime,'TARGET',base),patch.object(runtime,'CONFIG_HASH',runtime.digest(base)),patch.object(runtime,'WAKE_HASH',runtime.digest(wake)),patch.object(runtime,'command') as command:
    runtime.mount_config()
    command.assert_called_once_with('mount','--bind',str(wake),str(base))
    self.assertEqual(base.read_bytes(),b'base')
 def test_payloads_keep_18_and_ship_1a_overlay(self):
  paths=list((ROOT/'resources/payload').rglob('rtl8852bs_config'))
  self.assertEqual(len(paths),4)
  for path in paths:
   self.assertEqual(path.read_bytes()[28],0x18)
   self.assertEqual(common.digest(path),common.CONFIG_HASH)
  wake=ROOT/'resources/wake/rtl8852bs_config.wake'
  self.assertEqual(wake.read_bytes()[28],0x1a)
  self.assertEqual(common.digest(wake),common.WAKE_HASH)
 def test_maximum_and_duplicates(self):
  peers=['00:11:22:33:44:%02X'%i for i in range(10)]
  self.assertEqual(common.validate(dict(peers=peers,ethernet_off=True)),dict(remote_mode='auto',ethernet_off=True))
  for bad in [peers+['00:11:22:33:44:AA'],[peers[0]]*2,['bad']]:
   for validate in (common.validate,wake_install.validate):
    with self.assertRaises(ValueError):validate(dict(peers=bad,ethernet_off=False))
 def test_settings_migrate_without_saved_addresses(self):
  for validate in (common.validate,wake_install.validate):
   self.assertEqual(validate(dict(peers=['00:11:22:33:44:55'],ethernet_off=True)),
                    dict(remote_mode='auto',ethernet_off=True))
   self.assertEqual(validate(dict(remote_mode='auto',ethernet_off=False)),
                    dict(remote_mode='auto',ethernet_off=False))
   with self.assertRaises(ValueError): validate(dict(remote_mode='manual',ethernet_off=False))
 def test_connected_query_ignores_pending(self):
  output='Connections:\n< LE 00:11:22:33:44:01 handle 17 state 1 lm CENTRAL\n< LE 00:11:22:33:44:02 handle 0 state 5 lm CENTRAL'
  with patch.object(common,'command',return_value=output):
   self.assertEqual(common.connected_remotes(),{'00:11:22:33:44:01'})
 def test_dynamic_pairing_connection_priority_and_cap(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp); adapter='AA:BB:CC:DD:EE:FF'
   peers=['00:11:22:33:44:%02X'%i for i in range(12)]
   def info(peer,blocked=False,hid=True,paired=True):
    path=root/adapter/peer/'info';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('[General]\nName=Remote '+peer+'\nAddressType=public\nSupportedTechnologies=LE;\nBlocked='+str(blocked).lower()+'\nServices='+('00001812-0000-1000-8000-00805f9b34fb;' if hid else '')+'\n'+('[LongTermKey]\n' if paired else ''))
    return path
   for peer in peers: info(peer)
   info('00:11:22:33:44:AA',blocked=True)
   info('00:11:22:33:44:AB',hid=False)
   info('00:11:22:33:44:AC',paired=False)
   with patch.object(common,'BONDS',root),patch.object(common,'connected_remotes',return_value={peers[-1]}):
    self.assertEqual(common.automatic_peers(adapter),[peers[-1]]+peers[:9])
    (root/adapter/peers[-1]/'info').unlink()
    self.assertEqual(common.automatic_peers(adapter),peers[:10])
    new='00:11:22:33:44:FF';info(new)
    with patch.object(common,'connected_remotes',return_value={new}):
     self.assertEqual(common.automatic_peers(adapter),[new]+peers[:9])
 def test_rc173_excluded_before_cap_and_old_entry_removed(self):
  rc='00:11:22:33:44:FF'
  others=[dict(address='00:11:22:33:44:%02X'%i,name='Remote %d'%i,connected=False) for i in range(11)]
  for name in ('XiaoPaiRC_173',' xiaopairc_173 '):
   with patch.object(common,'paired_remotes',return_value=[dict(address=rc,name=name,connected=True)]+others):
    self.assertEqual(common.automatic_peers('adapter'),[p['address'] for p in others[:10]])
  calls=[]
  class Channel:
   def command(self,op,payload):calls.append((op,payload));return b'\0'
  with tempfile.TemporaryDirectory() as temp,patch.object(runtime,'boot_id',return_value='boot'),patch.object(runtime,'event'):
   with patch.object(runtime,'OWNED',Path(temp)/'owned.json'),patch.object(runtime,'automatic_peers',return_value=[others[0]['address']]):
    runtime.save(runtime.OWNED,dict(boot_id='boot',address='adapter',peers=[rc]))
    runtime.refresh_peers(Channel(),'adapter')
    payload=b'\0'+bytes.fromhex(rc.replace(':',''))[::-1]
    self.assertIn((0xfc7c,payload),calls)
    self.assertNotIn((0xfc7b,payload),calls)
    self.assertEqual(runtime.load(runtime.OWNED)['peers'],[others[0]['address']])
 def test_refresh_shares_ownership_and_retries_partial_updates(self):
  a,b,c=['00:11:22:33:44:%02X'%i for i in range(3)]
  class Channel:
   def __init__(self):self.entries=set();self.fail=False
   def command(self,op,payload):
    peer=':'.join('%02X'%x for x in payload[1:][::-1])
    if op==0xfc7c:self.entries.discard(peer)
    else:
     self.entries.add(peer)
     if self.fail:raise TimeoutError('response lost after add')
    return b'\0'
  h=Channel()
  with tempfile.TemporaryDirectory() as temp,patch.object(runtime,'boot_id',return_value='boot'),patch.object(runtime,'event'):
   with patch.object(runtime,'OWNED',Path(temp)/'owned.json'),patch.object(runtime,'automatic_peers',return_value=[a,b]) as peers:
    runtime.refresh_peers(h,'adapter');self.assertEqual(h.entries,{a,b})
    peers.return_value=[c];h.fail=True
    with self.assertRaises(TimeoutError):runtime.refresh_peers(h,'adapter')
    self.assertEqual(set(runtime.load(runtime.OWNED)['peers']),{a,b,c})
    peers.return_value=[b];h.fail=False
    runtime.refresh_peers(h,'adapter');self.assertEqual(h.entries,{b})
    peers.return_value=[]
    runtime.refresh_peers(h,'adapter');self.assertEqual(h.entries,set())
 def test_ui_does_not_freeze_pairing_list(self):
  from unittest.mock import Mock
  dialog=Mock();dialog.select.return_value=0
  with patch.object(wake_install,'ROOT','/nonexistent/aurora/'):
   self.assertEqual(wake_install.choose(dialog),dict(remote_mode='auto',ethernet_off=True))
  dialog.multiselect.assert_not_called()
 def test_scope(self):
  self.assertTrue(wake_install.supported(dict(branch='ng',board='6s',chip='rtl8852')))
  for branch,board,chip in [('no','4pro','ap6275p'),('ng','4pro','ap6275p')]:
   self.assertFalse(wake_install.supported(dict(branch=branch,board=board,chip=chip)))
 def test_branch_board_payloads_and_runtime_guards(self):
  for branch,kernel in [('ng','4.9.269'),('no','5.15.196')]:
   for board in ('6s','4pro'):
    profile=dict(branch=branch,board=board,chip='rtl8852')
    self.assertTrue(wake_install.supported(profile))
    files=wake_install.files(profile);extra=wake_install.extra_targets(profile)
    dt='sc2_s905x4_tencent_aurora_'+('6s' if board=='6s' else '4pro_rtl8852')+('_ng' if branch=='ng' else '_hs400')
    release='Amlogic-ng.arm' if branch=='ng' else 'DISTRO_DEVICE="Amlogic-no"'
    self.assertEqual(common.wake_platform(kernel,'aarch64',dt,release,False,'pd_ignore_unused'),branch)
    if branch=='no':
     self.assertFalse({'aurora_bt_native.ko','dtb.img','load-drivers.sh','config.ini'} & set(extra))
     self.assertEqual(common.wake_platform(kernel,'aarch64',dt,release,True,''),'no')
    else:
     self.assertNotIn('aurora_bt_native.ko',files)
     self.assertIn('aurora_bt_native.ko',extra)
     self.assertIn(('ng-4pro/' if board=='4pro' else '')+'dtb.img',files)
     with self.assertRaises(RuntimeError): common.wake_platform(kernel,'aarch64',dt,release,True,'pd_ignore_unused')
     with self.assertRaises(RuntimeError): common.wake_platform(kernel,'aarch64',dt,release,False,'')
    with self.assertRaises(RuntimeError):common.wake_platform('6.0','aarch64',dt,release,False,'pd_ignore_unused')
    with self.assertRaises(RuntimeError):common.wake_platform(kernel,'aarch64','sc2_s905x4_tencent_aurora_4pro_ap6275p',release,False,'pd_ignore_unused')
 def test_ng_no_recovery_does_not_load_kernel_module(self):
  from types import SimpleNamespace
  for kernel in ('4.9.269','5.15.196'):
   with tempfile.TemporaryDirectory() as temp,patch.object(runtime,'REPAIR_LOCK',Path(temp)/'lock'),patch.object(runtime,'BUDGET',Path(temp)/'budget.json'),patch.object(runtime,'boot_id',return_value='boot'),patch.object(runtime.os,'uname',return_value=SimpleNamespace(release=kernel)),patch.object(runtime,'HCI') as hci,patch.object(runtime,'command') as command,patch.object(runtime,'event'):
    runtime.native_reset('00:11:22:33:44:55','once')
    command.assert_called_once_with('hciconfig','hci0','reset',timeout=12)
    hci.return_value.identify.assert_called_once_with('00:11:22:33:44:55')
    self.assertEqual(runtime.load(runtime.BUDGET),dict(boot_id='boot',token='once'))
 def test_config_idempotent_preserve_boot(self):
  original="# comment\ncoreelec='quiet foo=1'\nrootopt=BOOT_IMAGE=KERNEL.img boot=LABEL=COREELEC disk=LABEL=STORAGE\n"
  changed=wake_install.boot_config(original)
  self.assertIn('rootopt=BOOT_IMAGE=KERNEL.img',changed)
  self.assertIn('quiet foo=1 pd_ignore_unused',changed)
  self.assertEqual(wake_install.boot_config(changed),changed)
  with self.assertRaises(ValueError):wake_install.boot_config('coreelec="quiet $other"\n')
 def test_resume_once_and_no_awake_reset(self):
  m=dict(boot_id='b',token='t',mono=100)
  self.assertFalse(runtime.eligible_resume({}, {}, 'b',101))
  self.assertTrue(runtime.eligible_resume(m,{},'b',101))
  self.assertFalse(runtime.eligible_resume(m,dict(boot_id='b',token='t'),'b',101))
  self.assertFalse(runtime.eligible_resume(m,{},'x',101))
  self.assertFalse(runtime.eligible_resume(m,{},'b',116))
 def test_no_keycodes_and_no_clear_all(self):
  calls=[]
  class Channel:
   def command(self,op,payload):calls.append((op,payload));return b'\0'
  common.sync_peers(Channel(),['01:02:03:04:05:06'])
  self.assertEqual(calls,[(0xfc7c,bytes.fromhex('00060504030201')),(0xfc7b,bytes.fromhex('00060504030201'))])
  self.assertNotIn(0xfc7d,[x[0] for x in calls])
 def test_remove_absent_entry(self):
  class Channel:
   def command(self,op,payload):return b'\x12' if op==0xfc7c else b'\0'
  common.sync_peers(Channel(),['01:02:03:04:05:06'])
 def test_ethernet_disabled_does_not_change_links(self):
  with patch.object(runtime,'ethernet_restore') as restore,patch.object(runtime,'command') as command:
   runtime.ethernet_down(False);restore.assert_called_once();command.assert_not_called()
 def test_restore_originally_down_noop(self):
  with patch.object(runtime,'load',return_value={}),patch.object(runtime,'boot_id',return_value='b'),patch.object(runtime,'command') as command:
   runtime.ethernet_restore();command.assert_not_called()
 def test_known_backup_paths(self):
  paths=wake_install.extra_targets()
  self.assertEqual(paths['config.ini'],'/flash/config.ini')
  self.assertEqual(paths['dtb.img'],'/flash/dtb.img')
  self.assertNotIn('/flash/aml_autoscript',paths.values())
 def test_unit_does_not_wait_on_units_it_restarts(self):
  text=(ROOT/'resources/wake/aurora6s-wake.service').read_text()
  after=[x for x in text.splitlines() if x.startswith('After=')]
  self.assertNotIn('bluetooth.service',' '.join(after))
  self.assertNotIn('rtkbt-firmware-aml.service',' '.join(after))
 def test_payload_manifest(self):wake_install.verify()


class InstallTransactionTests(unittest.TestCase):
 def test_failed_wake_copy_restores_flash_and_options(self):
  for branch in ('ng','no'):
   for board in ('6s','4pro'):
    with self.subTest(branch=branch,board=board):self.check_failed_install(branch,board)
 def test_removed_legacy_module_restored_on_late_failure(self):
  for board in ('6s','4pro'):
   with self.subTest(board=board):self.check_failed_install('ng',board,late=True)
 def check_failed_install(self,branch,board,late=False):
  import repair
  from types import SimpleNamespace
  from contextlib import ExitStack
  with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
   root=Path(temp);payload=root/'payload';source=root/'source';backup=root/'backups'
   key=branch+'/'+board+('/rtl8852' if board=='4pro' else '')+'/dtb.img'
   source.mkdir();(payload/key).parent.mkdir(parents=True)
   (payload/key).write_bytes(b'base-new')
   dtb=root/'dtb.img';dtb.write_bytes(b'original-dtb')
   config=root/'config.ini';config.write_text("coreelec='quiet'\n")
   settings=root/'settings.json';settings.write_text('original-options')
   (source/'dtb.img').write_bytes(b'wake-new');(source/'runtime.py').write_bytes(b'new-runtime')
   wake_files={'dtb.img':str(dtb),'runtime.py':str(root/'runtime.py')}
   (source/'aurora6s-wake.service').write_bytes(b'new-service')
   wake_files['aurora6s-wake.service']=str(root/'aurora6s-wake.service')
   extra=dict(wake_files,**{'config.ini':str(config),'settings.json':str(settings),
       'enable-link':str(root/'enabled'),'old-ethernet-hook':str(root/'old-ethernet'),
       'old-native-hook':str(root/'old-native'),'old-heartbeat-hook':str(root/'old-heartbeat')})
   legacy=root/'aurora_bt_native.ko'
   if branch=='ng':
    legacy.write_bytes(b'previous-module');extra['aurora_bt_native.ko']=str(legacy)
   if branch=='no':
    wake_files.pop('dtb.img');extra.pop('dtb.img');extra.pop('config.ini')
   profile=dict(branch=branch,board=board,chip='rtl8852')
   for obj,name,value in [(repair,'PAYLOAD',payload),(repair,'BACKUPS',backup),
       (repair,'check',lambda **kw:profile),(repair,'profile_targets',lambda _: {key:str(dtb)}),
       (repair,'backup_targets',lambda _: {key:str(dtb)}),
       (repair,'links',lambda _:{}),(repair,'cleanup_targets',lambda *a:{}),
       (repair,'flash_is_ro',lambda:True),(repair,'run',lambda *a,**kw:SimpleNamespace(stdout='inactive',returncode=0)),
       (wake_install,'SOURCE',source),(wake_install,'verify',lambda:None),(wake_install,'verify_firmware',lambda:None),
       (wake_install,'files',lambda profile=None:wake_files),(wake_install,'extra_targets',lambda profile=None:extra)]:
    stack.enter_context(patch.object(obj,name,value))
   real_copy=repair.atomic_copy;failed=False
   def fail_copy(src,dst):
    nonlocal failed
    if not late and Path(dst)==root/'runtime.py' and not failed:
     failed=True;raise OSError('simulated write failure')
    return real_copy(src,dst)
   stack.enter_context(patch.object(repair,'atomic_copy',side_effect=fail_copy))
   if late:
    def fail_reload(*args,**kw):
     nonlocal failed
     if args==('systemctl','daemon-reload') and not failed:
      self.assertFalse(legacy.exists())
      failed=True;raise OSError('simulated reload failure')
     return SimpleNamespace(stdout='inactive',returncode=0)
    stack.enter_context(patch.object(repair,'run',side_effect=fail_reload))
   with self.assertRaisesRegex(RuntimeError,'已恢复原文件'):
    repair.install(confirmed_board=board,wake_settings=dict(peers=[],ethernet_off=False))
   self.assertEqual(dtb.read_bytes(),b'original-dtb')
   self.assertEqual(config.read_text(),"coreelec='quiet'\n")
   self.assertEqual(settings.read_text(),'original-options')
   self.assertFalse((root/'runtime.py').exists())
   if branch=='ng':self.assertEqual(legacy.read_bytes(),b'previous-module')

if __name__=='__main__':unittest.main()
