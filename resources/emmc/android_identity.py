"""Read vendor build.prop from a temporary read-only super mapping on NG.

LP format: AOSP liblp metadata_format.h (Apache-2.0), also distributed at
https://github.com/tchebb/parse-android-dynparts/tree/master/liblp/include/liblp
No userdata decryption or persistent Android mounts are involved.
"""
import hashlib
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import uuid


def vendor_extents(raw, capacity):
    geom=raw[4096:4148]
    if len(geom)!=52 or struct.unpack_from('<II',geom)!=(0x616c4467,52):
        raise ValueError('Unsupported super geometry')
    if hashlib.sha256(geom[:8]+bytes(32)+geom[40:]).digest()!=geom[8:40]:
        raise ValueError('Super geometry checksum mismatch')
    maximum,slots,blocksize=struct.unpack_from('<III',geom,40)
    if not 4096<=maximum<=1024**2 or maximum%512 or not 1<=slots<=4 or blocksize!=4096:
        raise ValueError('Unsupported super metadata limits')
    header=raw[12288:]
    if len(header)<128:raise ValueError('Short super metadata')
    magic,major,minor,size=struct.unpack_from('<IHHI',header)
    if magic!=0x414c5030 or major!=10 or minor>2 or size not in (128,256):
        raise ValueError('Unsupported super metadata version')
    tables_size=struct.unpack_from('<I',header,44)[0]
    if size+tables_size>maximum or len(header)<size+tables_size:raise ValueError('Short super tables')
    if hashlib.sha256(header[:12]+bytes(32)+header[44:size]).digest()!=header[12:44]:
        raise ValueError('Super header checksum mismatch')
    tables=header[size:size+tables_size]
    if hashlib.sha256(tables).digest()!=header[48:80]:raise ValueError('Super table checksum mismatch')
    def entries(offset,expected):
        start,count,stride=struct.unpack_from('<III',header,offset)
        if stride!=expected or start+count*stride>len(tables):raise ValueError('Invalid super table bounds')
        return [tables[start+i*stride:start+(i+1)*stride] for i in range(count)]
    parts=entries(80,52);extents=entries(92,24);groups=entries(104,48);devices=entries(116,64)
    if len(devices)!=1:raise ValueError('Only single-device super supported')
    first,_,_,device_size,name,flags=struct.unpack('<QIIQ36sI',devices[0])
    if name.rstrip(b'\0')!=b'super' or device_size!=capacity or flags:
        raise ValueError('Super backing device mismatch')
    candidates=[]
    for part in parts:
        name,attrs,start,count,group=struct.unpack('<36sIIII',part)
        if name.rstrip(b'\0') not in (b'vendor',b'vendor_a',b'vendor_b') or not count:continue
        if attrs & ~5 or group>=len(groups) or start+count>len(extents):raise ValueError('Unsupported vendor partition')
        rows=[]
        for item in extents[start:start+count]:
            sectors,kind,offset,source=struct.unpack('<QIQI',item)
            if kind!=0 or source!=0 or sectors==0 or offset<first or (offset+sectors)*512>capacity:
                raise ValueError('Invalid vendor extent')
            if any(offset<oldoff+oldsize and oldoff<offset+sectors for oldsize,oldoff in rows):
                raise ValueError('Overlapping vendor extents')
            rows.append((sectors,offset))
        candidates.append(rows)
    if len(candidates)!=1:raise ValueError('Cannot unambiguously select original Android vendor')
    return candidates[0]


def read_vendor_models(super_row):
    dev=Path('/dev/super')
    import os,stat
    if not stat.S_ISBLK(dev.stat().st_mode):raise ValueError('super is not a block device')
    block=Path('/sys/dev/block/%d:%d'%(os.major(dev.stat().st_rdev),os.minor(dev.stat().st_rdev)))
    if block.resolve().parent.name!='mmcblk0' or int((block/'start').read_text())*512!=super_row['offset'] or int((block/'size').read_text())*512!=super_row['size']:
        raise ValueError('super device does not match verified MPT')
    with dev.open('rb',buffering=0) as f:raw=f.read(12288+1024**2)
    extents=vendor_extents(raw,super_row['size'])
    def run(args):
        p=subprocess.run(args,capture_output=True,encoding='utf-8',timeout=30)
        if p.returncode:raise ValueError('Android 机型读取失败：'+p.stderr.strip())
        return p.stdout.strip()
    name='aurora-id-'+uuid.uuid4().hex[:12]
    cursor=0;table=[]
    for size,offset in extents:
        table.append('%d %d linear %s %d'%(cursor,size,dev,offset));cursor+=size
    with tempfile.TemporaryDirectory(prefix='aurora-android-') as tmp:
        created=mounted=False
        try:
            run(['dmsetup','create',name,'--readonly','--table','\n'.join(table)]);created=True
            run(['mount','-t','ext4','-o','ro,noload','/dev/mapper/'+name,tmp]);mounted=True
            text=(Path(tmp)/'build.prop').read_text(encoding='utf-8')
            return sorted({line.split('=',1)[1].strip() for line in text.splitlines()
                           if re.match(r'^ro\.product\.(?:[\w]+\.)?model=',line)})
        finally:
            if mounted:run(['umount',tmp])
            if created:run(['dmsetup','remove','--retry',name])
