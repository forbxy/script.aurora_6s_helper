#!/usr/bin/env python3
"""Foreground, metadata-only OTA recovery adapted from validated standalone 0.1.2."""
import contextlib,fcntl,hashlib,json,os,re,shutil,stat,struct,subprocess,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
import operations as op
import policy
from aurora_emmc import read
from layout_trial import read_regions,write_regions,atomic_json,private_external
from prepare_boot import validate_stock_cfgload,ROOTOPT
from ota_repair_core import discover_layout,select_slot,check_environment,bump_dtb,sha,geometry,validate_kernel_header,validate_env_config

PLAN_SCHEMA=2
REPAIR_MODE='layout-only'
def run(args,timeout=120):
    p=subprocess.run(args,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError(' '.join(map(str,args))+': '+p.stderr.decode('utf-8',errors='replace'))
    return p.stdout.decode('utf-8',errors='strict').strip()
def env_dump():
    # Explicit read-only environment command; no environment writer in this entry point.
    config_text=validate_env_config(read('/etc/fw_env.config'))
    with tempfile.TemporaryDirectory(prefix='aurora-repair-env-') as folder:
        config=Path(folder)/'fw_env.config'
        config.write_text(config_text,encoding='ascii')
        return run(['fw_printenv','-c',str(config)])

def env_read():
    out=env_dump();env={}
    for line in out.splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            env[k]=v
    if any(k not in env for k in op.ENV_KEYS):raise ValueError('Missing required boot environment')
    return env

def check_env_device(report):
    # Use an explicit config for /dev/env; never let fw_env.config target SPI/another disk.
    dev=os.stat('/dev/env')
    if not stat.S_ISBLK(dev.st_mode):raise ValueError('Invalid env device')
    p=Path('/sys/dev/block/%d:%d'%(os.major(dev.st_rdev),os.minor(dev.st_rdev))).resolve()
    row=report['mpt']['partitions'][3]
    if row['name']!='env' or p.parent.name!='mmcblk0' or int(read(p/'start'))*512!=row['offset'] or int(read(p/'size'))*512!=row['size']:
        raise ValueError('env partition identity mismatch')
    validate_env_config(read('/etc/fw_env.config'))

def read_at(offset,size):
    with open(op.DISK,'rb',buffering=0) as f:f.seek(offset);data=f.read(size)
    if len(data)!=size:raise ValueError('Short eMMC read')
    return data

@contextlib.contextmanager
def mounted(row,report,fs):
    tmp=tempfile.mkdtemp(prefix='aurora-repair-ro-');mounted_ok=False
    try:
        with op.region_loop(row,report,readonly=True) as dev:
            if read('/sys/class/block/'+dev.name+'/ro')!='1':raise ValueError('Mapping not read-only')
            try:
                run(['mount','-t',fs,'-o','ro,noload' if fs=='ext4' else 'ro',str(dev),tmp]);mounted_ok=True
                yield Path(tmp),dev
            finally:
                if mounted_ok or os.path.ismount(tmp):run(['umount',tmp]);mounted_ok=False
    finally:
        if not os.path.ismount(tmp):
            try:os.rmdir(tmp)
            except OSError:pass

def file_hash(p,update,prefix):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while b:=f.read(4*1024**2):
            h.update(b);update('repair-check','校验 CE 启动文件：'+p.name,progress_step=prefix+':sha:'+p.name,progress_done=f.tell(),progress_total=p.stat().st_size)
    return h.hexdigest()

def filesystem_check(report,target,update,prefix):
    boot,data=target[-3:-1];evidence={}
    for row,fs in [(boot,'vfat'),(data,'ext4')]:
        with op.region_loop(row,report,readonly=True) as dev:
            if read('/sys/class/block/'+dev.name+'/ro')!='1':raise ValueError('Mapping not read-only')
            fatcheck=shutil.which('fsck.fat') or shutil.which('fsck.vfat')
            if fs=='vfat' and not fatcheck:raise ValueError('Missing read-only FAT checker (fsck.fat/fsck.vfat)')
            args=[fatcheck,'-n',str(dev)] if fs=='vfat' else ['e2fsck','-f','-n',str(dev)]
            p=run_fsck(args,update,row['name'])
            evidence[row['name']+'-fsck']={'exit':p.returncode,'log':(p.stdout+p.stderr).decode('utf-8',errors='replace')}
            if p.returncode:raise ValueError('Read-only filesystem check failed: '+row['name']+'\n'+evidence[row['name']+'-fsck']['log'])
    with mounted(boot,report,'vfat') as (path,_):
        names=['kernel.img','SYSTEM','dtb.img','config.ini','cfgload']
        for name in names:
            p=path/name
            if not p.is_file() or p.is_symlink() or p.stat().st_size==0:raise ValueError('Missing boot file: '+name)
        update('repair-check','核对 CE 启动文件',progress_plan={**{stage+':sha:'+n:(path/n).stat().st_size for stage in ('repair-prepare','repair-recheck') for n in names},**{stage+':md5:'+n:(path/n).stat().st_size for stage in ('repair-prepare','repair-recheck') for n in ('kernel.img','SYSTEM')},'repair-metadata:dtb':1048576,'repair-metadata:mpt':8192})
        validate_stock_cfgload((path/'cfgload').read_bytes())
        roots=[l.split('=',1)[1] for l in (path/'config.ini').read_text(encoding='utf-8').splitlines() if re.match(r'^rootopt=',l)]
        if roots!=[ROOTOPT]:raise ValueError('Unexpected internal rootopt')
        for name in ('kernel.img','SYSTEM'):
            with (path/name).open('rb') as f:magic=f.read(64)
            if name=='SYSTEM' and magic[:4]!=b'hsqs':raise ValueError('Invalid SYSTEM header')
            if name=='kernel.img':validate_kernel_header(magic,(path/name).stat().st_size)
            md=path/(name+'.md5')
            if not md.is_file() or md.is_symlink():raise ValueError('Missing internal MD5: '+name)
            expected=md.read_text(encoding='ascii').split()[0]
            if not re.fullmatch('[0-9a-fA-F]{32}',expected):raise ValueError('Invalid MD5 sidecar')
            h=hashlib.md5()
            with (path/name).open('rb') as f:
                while b:=f.read(4*1024**2):
                    h.update(b);update('repair-check','核对 CE 原校验值：'+name,progress_step=prefix+':md5:'+name,progress_done=f.tell(),progress_total=(path/name).stat().st_size)
            if h.hexdigest()!=expected.lower():raise ValueError('Boot payload MD5 mismatch: '+name)
        evidence['boot_files']={n:file_hash(path/n,update,prefix) for n in names}
    with mounted(data,report,'ext4') as (path,_):
        if not (path/'.kodi').is_dir() or not (path/'.config').is_dir():raise ValueError('CE data directories missing')
        evidence['data']={'kodi':True,'config':True}
    return evidence

def inspect_state(update,prefix):
    report=op.probe()
    if report['os_release'].get('DISTRO_DEVICE')!='Amlogic-no':raise ValueError('First test supports CE NO 22.x only')
    if report['blockers']:raise ValueError('; '.join(report['blockers']))
    policy.identity(report);op.external(report);op.target_idle(report);check_env_device(report)
    with open(op.DISK,'rb',buffering=0) as f:current=read_regions(f.fileno())
    policy.validate_dtb_layout(current['dtb'],report['mpt']['partitions'])
    target,inference=discover_layout(report,read_at)
    miscrow=next(p for p in report['mpt']['partitions'] if p['name']=='misc')
    misc=read_at(miscrow['offset'],65536);slot=select_slot(misc);env=env_read()
    check_environment(env)
    update('repair-check','只读检查残留 CE 文件系统；此阶段进度按检查阶段显示')
    evidence=filesystem_check(report,target,update,prefix)
    # Detect changes during the read-only checks, not just at apply time.
    if current!={k:read_at(off,n) for k,off,n in [('dtb',40*1024**2,524288),('mpt',36*1024**2,4096)]} or misc!=read_at(miscrow['offset'],65536) or env!=env_read():raise ValueError('Metadata changed during inspection')
    info={'schema':PLAN_SCHEMA,'repair_mode':REPAIR_MODE,'boot_id':read('/proc/sys/kernel/random/boot_id'),'cid':report['cid'],'emmc_bytes':report['emmc_bytes'],
          'model':report['android_models'],'source_hashes':{k:sha(v) for k,v in current.items()},'slot':slot,'environment_before':env,'environment_changes':{},
          'partitions':target,'inference':inference,'filesystems':evidence}
    return report,current,info

def store(path,data):
    with path.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    if path.read_bytes()!=data:raise ValueError('Local readback failure')

def prepare(folder,expected,update):
    report,current,info=inspect_state(update,'repair-prepare')
    op.same_device(expected,report)
    if os.statvfs('/storage').f_bavail*os.statvfs('/storage').f_frsize<64*1024**2:raise ValueError('Need 64MiB free external space')
    folder.mkdir(mode=0o700);private_external(folder)
    old,new=op.make_metadata(report,info['partitions'],folder/'metadata')
    if old!=current:raise ValueError('Metadata changed while generating candidate')
    new['dtb']=bump_dtb(current['dtb'],new['dtb'])
    policy.metadata_check(current['dtb'],new['dtb'],new['mpt'],report,info['partitions'])
    for k,v in new.items():store(folder/('repair-'+k+'.bin'),v)
    preserved={}
    for row in preserved_rows(report):
        raw=read_at(row['offset'],row['size']);store(folder/('before-'+row['name']+'.bin'),raw)
        preserved[row['name']]=sha(raw)
    for key,raw in current.items():store(folder/('before-'+key+'.bin'),raw)
    info['preserved_sha256']=preserved;info['candidate_hashes']={k:sha(v) for k,v in new.items()}
    info['runtime_sha256']=runtime_hash()
    atomic_json(folder/'plan.json',info);atomic_json(folder/'status.json',{'phase':'prepared','device_writes_started':False})
    return folder

def validate_plan_scope(plan):
    if plan.get('schema')!=PLAN_SCHEMA or plan.get('repair_mode')!=REPAIR_MODE:
        raise ValueError('Requires a new layout-only plan; run inspect with this version (old plans are rejected)')
    if plan.get('environment_changes')!={}:
        raise ValueError('Layout-only repair forbids environment changes')

def preserved_rows(report):
    return [next(p for p in report['mpt']['partitions'] if p['name']==name) for name in ('env','misc')]

def verify_preserved(fd,report,plan):
    for row in preserved_rows(report):
        data=os.pread(fd,row['size'],row['offset'])
        if len(data)!=row['size'] or sha(data)!=plan['preserved_sha256'][row['name']]:
            raise ValueError('Preserved region changed: '+row['name'])


def apply(folder,confirm,update):
    if not confirm:raise ValueError('Explicit --confirm-repair required; inspect the plan first')
    folder=private_external(folder);plan=json.loads((folder/'plan.json').read_text())
    validate_plan_scope(plan)
    status=json.loads((folder/'status.json').read_text())
    if status['phase']!='prepared':raise ValueError('Plan already used or interrupted; do not replay')
    if plan['runtime_sha256']!=runtime_hash():raise ValueError('Different repair runtime')
    report,current,now=inspect_state(update,'repair-recheck')
    for key in ['schema','repair_mode','boot_id','cid','emmc_bytes','model','source_hashes','slot','environment_before','environment_changes','partitions','filesystems']:
        # fsck textual logs contain elapsed time; compare actual boot hashes separately.
        if key=='filesystems':
            if now[key]['boot_files']!=plan[key]['boot_files']:raise ValueError('CE boot files changed')
        elif now[key]!=plan[key]:raise ValueError('Plan stale: '+key)
    new={k:(folder/('repair-'+k+'.bin')).read_bytes() for k in ('dtb','mpt')}
    if {k:sha(v) for k,v in new.items()}!=plan['candidate_hashes']:raise ValueError('Candidate changed')
    policy.metadata_check(current['dtb'],new['dtb'],new['mpt'],report,now['partitions'])
    for row in preserved_rows(report):
        name=row['name']
        if sha(read_at(row['offset'],row['size']))!=plan['preserved_sha256'][name] or sha((folder/('before-'+name+'.bin')).read_bytes())!=plan['preserved_sha256'][name]:
            raise ValueError('Preserved region changed or backup damaged: '+name)
    for key,raw in current.items():
        if (folder/('before-'+key+'.bin')).read_bytes()!=raw:raise ValueError('Metadata backup damaged: '+key)
    op.external(report);op.target_idle(report);check_env_device(report)
    # Kernel partition view is intentionally NOT reloaded. Reboot required afterwards.
    def record(phase):
        update('repair-writing','恢复分区描述并读回校验（不修改槽位和用户数据）',device_writes_started=True)
        atomic_json(folder/'status.json',{'phase':phase,'device_writes_started':True,'reboot_required':True})
        if phase.startswith('verified-'):
            key=phase.split('-',1)[1]
            update('repair-writing','分区元数据已读回核对：'+key,progress_step='repair-metadata:'+key,progress_done=2*len(new[key]),progress_total=2*len(new[key]))
    update('repair-check','写入前最后核对；仅恢复 DTB/MPT',progress_plan={'repair-metadata:'+k:2*len(v) for k,v in new.items()})
    fd=None
    try:
        fd=os.open(op.DISK,os.O_RDWR|os.O_CLOEXEC)
        if not stat.S_ISBLK(os.fstat(fd).st_mode):raise ValueError('Not an eMMC block device')
        capacity=struct.unpack('Q',fcntl.ioctl(fd,op.BLKGETSIZE64,b'\0'*8))[0]
        if capacity!=plan['emmc_bytes'] or read('/sys/block/mmcblk0/device/cid')!=plan['cid']:raise ValueError('Device identity changed')
        validate_plan_scope(plan)
        if env_read()!=plan['environment_before']:raise ValueError('Environment changed before write')
        verify_preserved(fd,report,plan)
        write_regions(fd,current,new,record)
        verify_preserved(fd,report,plan)
        if env_read()!=plan['environment_before']:raise ValueError('Environment changed after metadata write')
        if read_regions(fd)!=new:raise ValueError('Final metadata readback mismatch')
        os.sync();atomic_json(folder/'status.json',{'phase':'complete','device_writes_started':True,'reboot_required':True})

    except BaseException as exc:
        prior=json.loads((folder/'status.json').read_text())
        atomic_json(folder/'status.json',{'phase':'needs-review' if prior.get('device_writes_started') else 'failed-before-write','device_writes_started':prior.get('device_writes_started',False),'error':str(exc),'backup':str(folder)})
        raise
    finally:
        if fd is not None:os.close(fd)


def runtime_hash():
    h=hashlib.sha256()
    for path in sorted(ROOT.glob('*.py'))+[ROOT/'bin/ampart']:
        h.update(path.relative_to(ROOT).as_posix().encode()+b'\0');h.update(path.read_bytes())
    return h.hexdigest()


def run_fsck(args,update,name):
    # Poll only to update the foreground UI/cancellation, never invent byte counts.
    with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        started=time.monotonic()
        try:
            while True:
                update('repair-check','只读检查文件系统：'+name+'（尚未写入 eMMC）')
                try:
                    stdout,stderr=process.communicate(timeout=1)
                    return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
                except subprocess.TimeoutExpired:
                    if time.monotonic()-started>1800:raise TimeoutError('文件系统只读检查超时：'+name)
        finally:
            if process.poll() is None:process.kill();process.communicate()


def assess(report):
    """Bounded read-only preview for UI/submit; full checks run in foreground."""
    from platform_support import branch
    if branch(report['os_release'])!='Amlogic-no':
        raise ValueError('OTA 布局修复目前仅支持 NO 22.x；尚未验证 NG')
    if report['blockers']:raise ValueError('; '.join(report['blockers']))
    check_env_device(report)
    target,inference=discover_layout(report,read_at)
    env=env_read();check_environment(env)
    misc=next(p for p in report['mpt']['partitions'] if p['name']=='misc')
    slot=select_slot(read_at(misc['offset'],65536))
    report['ota_repair']={'repair_mode':REPAIR_MODE,'environment_changes':{},'partitions':target,
        'inference':inference,'misc_selected_slot':slot['selected']}
    return report


def execute(folder,report,update):
    # Worker owns both eMMC locks, confirmation, lifecycle and final job state.
    prepared=prepare(folder/'layout-repair',report,update)
    plan=json.loads((prepared/'plan.json').read_text())
    if geometry(plan['partitions'])!=geometry(report['ota_repair']['partitions']):
        raise ValueError('恢复布局与确认时不同，请重新检查')
    apply(prepared,True,update)
