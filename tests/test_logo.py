"""No block-device access: codec and Logo-only writer tests on ordinary files."""
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]/'resources/logo'
sys.path.insert(0,str(ROOT))
import codec
spec=importlib.util.spec_from_file_location('logo_backend',ROOT/'backend.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
try:
 from PIL import Image
except ImportError:
 Image=None

class LogoCodecTests(unittest.TestCase):
 def setUp(self):self.raw=codec.stock(ROOT/'stock-logo.img.gz')
 def test_stock_hash_and_roundtrip(self):
  self.assertEqual(codec.digest(self.raw),codec.STOCK_SHA256)
  self.assertEqual(codec.pack(codec.unpack(self.raw)),self.raw)
  self.assertLess((ROOT/'stock-logo.img.gz').stat().st_size,100*1024)
 def test_corruption_and_bounds(self):
  for pos in (0,8,16,100,50000):
   raw=bytearray(self.raw);raw[pos]^=1
   with self.assertRaises(ValueError):codec.unpack(raw)
  with self.assertRaises(ValueError):codec.unpack(self.raw[:-1])
 def test_zipbomb_limited(self):
  with self.assertRaises(ValueError):codec.gunzip(gzip.compress(b'A'*10000),100)
 def test_stock_hash_guard(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'stock.gz';p.write_bytes(gzip.compress(b'X'*codec.PARTITION_SIZE))
   with self.assertRaisesRegex(ValueError,'素材校验'):codec.stock(p)
 @unittest.skipUnless(Image,'Pillow image integration')
 def test_rgb565_and_other_resources_preserved(self):
  image=Image.new('RGB',(1920,1080),'blue');data=io.BytesIO();image.save(data,format='PNG')
  candidate=codec.replace_boot(self.raw,data.getvalue());entries=dict(codec.unpack(candidate))
  for name,payload in codec.unpack(self.raw):
   if name!='bootup':self.assertEqual(entries[name],payload)
  bmp=gzip.decompress(entries['bootup'])
  self.assertEqual(struct.unpack_from('<I',bmp,10)[0],70)
  self.assertEqual(struct.unpack_from('<I',bmp,14)[0],56)
  self.assertEqual(struct.unpack_from('<H',bmp,28)[0],16)
  self.assertEqual(struct.unpack_from('<I',bmp,30)[0],3)
  self.assertEqual(codec.boot_image(candidate).getpixel((960,540)),(0,0,255))
 @unittest.skipUnless(Image,'Pillow image integration')
 def test_transparency_and_letterbox(self):
  image=Image.new('RGBA',(100,200),(255,0,0,0));data=io.BytesIO();image.save(data,format='PNG')
  result=codec.decode(data.getvalue());self.assertEqual(result.size,(1920,1080));self.assertEqual(result.getpixel((960,540)),(0,0,0))
 @unittest.skipUnless(Image,'Pillow image integration')
 def test_unsupported_image_rejected(self):
  data=io.BytesIO();Image.new('RGB',(16,16)).save(data,format='GIF')
  with self.assertRaisesRegex(ValueError,'PNG'):codec.decode(data.getvalue())

class LogoWriterTests(unittest.TestCase):
 def test_explicit_confirmation(self):
  with patch.object(b,'locked') as lock:
   with self.assertRaises(ValueError):b.apply('unused','unused',False)
   lock.assert_not_called()
 def test_full_write_limited_to_logo_file(self):
  raw=codec.stock(ROOT/'stock-logo.img.gz')
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);folder=base/'task';folder.mkdir();disk=base/'logo';before=b'\0'*codec.PARTITION_SIZE;disk.write_bytes(before)
   st=disk.stat();identity={'dev':'%d:%d'%(os.major(st.st_rdev),os.minor(st.st_rdev))}
   plan={'mode':'stock','identity':identity,'old_sha256':codec.digest(before),'new_sha256':codec.digest(raw),'preview_sha256':codec.digest(b'preview')}
   (folder/'plan.json').write_text(json.dumps(plan));(folder/'status.json').write_text('{"phase":"prepared"}')
   (folder/'candidate.img').write_bytes(raw);(folder/'before.img.gz').write_bytes(gzip.compress(before));(folder/'preview.png').write_bytes(b'preview')
   with contextlib.ExitStack() as stack:
    stack.enter_context(patch.object(b,'locked',return_value=contextlib.nullcontext()))
    stack.enter_context(patch.object(b,'BASE',base));stack.enter_context(patch.object(b,'DEVICE',str(disk)))
    stack.enter_context(patch.object(b,'identify',return_value=identity))
    stack.enter_context(patch.object(b.stat,'S_ISBLK',return_value=True))
    stack.enter_context(patch.object(b.fcntl,'ioctl',return_value=struct.pack('Q',codec.PARTITION_SIZE)))
    # Candidate and stale disk changes refuse before pwrite.
    with patch.object(b.os,'pwrite',side_effect=AssertionError('must not write')):
     with self.assertRaisesRegex(ValueError,'预览不一致'):b.apply(str(folder),'wrong',True)
     disk.write_bytes(b'Z'*codec.PARTITION_SIZE)
     with self.assertRaisesRegex(ValueError,'内容或容量'):b.apply(str(folder),codec.digest(raw),True)
    disk.write_bytes(before);(folder/'status.json').write_text('{"phase":"prepared"}')
    result=b.apply(str(folder),codec.digest(raw),True)
    self.assertEqual(disk.read_bytes(),raw);self.assertIn('读回校验',result['message'])
    self.assertEqual(gzip.decompress((folder/'before.img.gz').read_bytes()),before)
    with self.assertRaisesRegex(ValueError,'已执行'):b.apply(str(folder),codec.digest(raw),True)
 def test_partial_writes_retried_and_readback(self):
  with tempfile.TemporaryFile() as f:
   raw=b'X'*codec.PARTITION_SIZE;f.truncate(len(raw));real=os.pwrite
   with patch.object(b.os,'pwrite',side_effect=lambda fd,data,off:real(fd,data[:65536],off)):
    b.write_verified(f.fileno(),raw)
   self.assertEqual(os.pread(f.fileno(),len(raw),0),raw)
   with patch.object(b,'read_logo',return_value=b'wrong'):
    with self.assertRaisesRegex(ValueError,'读回校验'):b.write_verified(f.fileno(),raw)


class LegacyBMPTests(unittest.TestCase):
 def test_stock_header_promotion_preserves_pixels(self):
  bmp=gzip.decompress(dict(codec.unpack(codec.stock(ROOT/'stock-logo.img.gz')))['bootup'])
  compatible=codec.pillow_bytes(bmp);offset=struct.unpack_from('<I',bmp,10)[0]
  self.assertEqual(struct.unpack_from('<I',compatible,14)[0],108)
  self.assertEqual(struct.unpack_from('<I',compatible,10)[0],offset+52)
  self.assertEqual(compatible[54:70],bmp[54:70])
  self.assertEqual(compatible[offset+52:],bmp[offset:])
  self.assertEqual(struct.unpack_from('<I',bmp,14)[0],56)
 def test_invalid_headers(self):
  bmp=bytearray(gzip.decompress(dict(codec.unpack(codec.stock(ROOT/'stock-logo.img.gz')))['bootup']))
  with self.assertRaises(ValueError):codec.pillow_bytes(bytes(bmp[:69]))
  for offset in (0,54,len(bmp)+1):
   struct.pack_into('<I',bmp,10,offset)
   with self.assertRaises(ValueError):codec.pillow_bytes(bytes(bmp))
 @unittest.skipUnless(Image,'Pillow integration')
 def test_rgb565_padding_and_both_orientations(self):
  top=struct.pack('<3H',0xf800,0x07e0,0x001f)+b'\0\0'
  bottom=struct.pack('<3H',0xffff,0,0xffe0)+b'\0\0'
  real_open=Image.open
  def legacy_open(stream,*args,**kwargs):
   data=stream.getvalue()
   if data[:2]==b'BM' and struct.unpack_from('<I',data,14)[0]==56:
    raise OSError('Unsupported BMP header type (56)')
   return real_open(stream,*args,**kwargs)
  for height,pixels in ((2,bottom+top),(-2,top+bottom)):
   header=bytearray(70)
   struct.pack_into('<2sIHHI',header,0,b'BM',86,0,0,70)
   struct.pack_into('<IiiHHIIiiII',header,14,56,3,height,1,16,3,16,0,0,0,0)
   struct.pack_into('<4I',header,54,0xf800,0x07e0,0x001f,0)
   with patch.object(Image,'open',side_effect=legacy_open):
    with Image.open(io.BytesIO(codec.pillow_bytes(bytes(header)+pixels))) as img:
     self.assertEqual(list(img.getdata()),[(255,0,0),(0,255,0),(0,0,255),(255,255,255),(0,0,0),(255,255,0)])
    self.assertEqual(codec.decode(bytes(header)+pixels).size,(1920,1080))

if __name__=='__main__':unittest.main()

class LogoUITests(unittest.TestCase):
 def test_preview_callbacks_use_control_ids(self):
  import types
  from unittest.mock import MagicMock
  spec=importlib.util.spec_from_file_location('logo_ui_callbacks_test',ROOT.parent/'lib/logo_ui.py')
  ui=importlib.util.module_from_spec(spec)
  gui=types.ModuleType('xbmcgui');gui.WindowDialog=object
  with patch.dict(sys.modules,{'xbmcgui':gui,'xbmc':types.ModuleType('xbmc'),'xbmcvfs':types.ModuleType('xbmcvfs')}):spec.loader.exec_module(ui)
  def control(value):return types.SimpleNamespace(getId=lambda:value)
  for writable,clicked,closed,accepted in ((False,101,True,False),(True,101,True,False),(True,102,True,True),(False,102,False,False),(True,999,False,False)):
   view=object.__new__(ui.Preview)
   view.writable=writable;view.accepted=False;view.back=control(101)
   if writable:view.next=control(102)
   view.close=MagicMock()
   callback=control(clicked)
   self.assertIsNot(callback,view.back)
   view.onControl(callback)
   self.assertEqual(view.close.called,closed)
   self.assertEqual(view.accepted,accepted)
  for action_id in (9,10,92,216):
   view.accepted=False;view.close.reset_mock()
   view.onAction(control(action_id))
   view.close.assert_called_once();self.assertFalse(view.accepted)

 def test_preview_cancel_and_final_confirmation(self):
  import types
  from unittest.mock import MagicMock
  path=ROOT.parent/'lib/logo_ui.py'
  for mode,accepted,confirmed in ((0,False,False),(2,False,False),(2,True,False),(2,True,True)):
   spec=importlib.util.spec_from_file_location('logo_ui_test',path);ui=importlib.util.module_from_spec(spec)
   gui=types.ModuleType('xbmcgui');gui.WindowDialog=object
   with patch.dict(sys.modules,{'xbmcgui':gui,'xbmc':types.ModuleType('xbmc'),'xbmcvfs':types.ModuleType('xbmcvfs')}):spec.loader.exec_module(ui)
   dialog=MagicMock();dialog.select.return_value=mode;dialog.yesno.return_value=confirmed
   result=dict(folder='/storage/.config/aurora-logo/test',preview='/tmp/image.png',new_sha256='expected')
   view=MagicMock();view.accepted=accepted
   with patch.object(gui,'Dialog',return_value=dialog,create=True),patch.object(gui,'DialogProgressBG',return_value=MagicMock(),create=True),patch.object(ui,'Preview',return_value=view),patch.object(ui,'discard') as discard,patch.object(ui,'call',side_effect=[result,{'message':'done','backup':'backup'}]) as call:
    ui.main();discard.assert_called_once_with(result)
    if accepted and confirmed:
     self.assertEqual(call.call_args.args,('apply',result['folder'],'--sha256','expected','--confirm'))
    else:self.assertEqual(call.call_count,1)
