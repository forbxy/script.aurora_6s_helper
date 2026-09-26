"""User-initiated eMMC management; the lighting service never installs/restores."""
import json
import os
from pathlib import Path
import subprocess
import time
import xbmc
import xbmcgui

WORKER=Path(__file__).resolve().parents[1]/'emmc/worker.py'
TITLES={'install':'安装 Android / CE 双系统','remove':'移除 eMMC 中的 CE','backup':'完整备份 eMMC','restore':'从完整备份还原 eMMC','repair':'修复 OTA 后的双系统布局（NO）'}
OPERATION_NOTICE='操作期间请勿取消、断电、拔盘、退出 Kodi 或进行任何其他操作，请等待完成。'


def operation_message(message):
    return message+'\n'+OPERATION_NOTICE


def call(*args):
    p=subprocess.run(['/usr/bin/python3',str(WORKER),*args],capture_output=True,encoding='utf-8',timeout=90,
                     env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    if p.returncode:raise RuntimeError(p.stderr.strip() or p.stdout.strip() or 'eMMC 操作失败')
    return json.loads(p.stdout)


def show_status():
    state=call('status');text=state['message']
    if state.get('phase') not in (None,'none','idle','complete','failed','needs-recovery','cancelled'):
        text=operation_message(text)
    if state.get('backup'):text+='\n备份：'+state['backup']
    if state.get('job'):text+='\n日志：'+state['job']+'/worker.log'
    xbmcgui.Dialog().ok('eMMC 任务状态',text)


def run_foreground(result, title):
    job=Path(result['job']);progress=xbmcgui.DialogProgress()
    progress.create(title,operation_message('准备执行…'))
    monitor=xbmc.Monitor();process=None;cancel_sent=False
    try:
        with (job/'worker.log').open('ab',buffering=0) as log:
            process=subprocess.Popen(['/usr/bin/python3',result['runtime'],'run',str(job),
                '--parent-pid',str(os.getpid())],stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            while process.poll() is None:
                state=json.loads((job/'status.json').read_text(encoding='utf-8'))
                message=state['message'];total=state.get('total_bytes',0)
                if total:
                    message+='\n已处理 %.2f / %.2f GiB（含校验）' % (state.get('processed_bytes',0)/1024**3,total/1024**3)
                if progress.iscanceled():
                    if not state.get('device_writes_started') and not cancel_sent:
                        (job/'cancel').touch();cancel_sent=True
                    progress.close();progress=xbmcgui.DialogProgress()
                    progress.create(title,operation_message('等待安全取消…' if cancel_sent else '正在写入，不能中途取消'))
                progress.update(state.get('percent',0),operation_message(message))
                if monitor.waitForAbort(0.25):
                    # Kodi shutdown: child records interruption; never detach it as a job.
                    process.terminate();process.wait();return
            state=json.loads((job/'status.json').read_text(encoding='utf-8'))
            if state['phase'] not in ('complete','failed','needs-recovery','cancelled'):
                state.update(phase='needs-recovery' if state.get('device_writes_started') else 'failed',
                             message='前台进程异常退出，请查看日志',exit_code=process.returncode)
                tmp=job/'status.json.tmp';tmp.write_text(json.dumps(state));tmp.replace(job/'status.json')
    except Exception as exc:
        if process is None:
            state=json.loads((job/'status.json').read_text(encoding='utf-8'));state.update(phase='failed',message='前台启动失败：'+str(exc))
            tmp=job/'status.json.tmp';tmp.write_text(json.dumps(state));tmp.replace(job/'status.json')
        if process is not None and process.poll() is None:
            state=json.loads((job/'status.json').read_text(encoding='utf-8'))
            if not state.get('device_writes_started'):(job/'cancel').touch()
            # Keep ownership if UI updating fails while the disk writer is active.
            process.wait()
        raise
    finally:progress.close()
    show_status()


def main():
    dialog=xbmcgui.Dialog()
    choice=dialog.select('eMMC 双系统与备份',list(TITLES.values())+['查看任务状态'])
    if choice<0:return
    if choice==len(TITLES):show_status();return
    action=list(TITLES)[choice];backup=None
    if action=='restore':
        rows=call('backups')
        if not rows:raise RuntimeError('未找到完整备份。备份目录为外置 /storage/aurora-emmc-backups/')
        i=dialog.select('选择完整备份',[Path(r['path']).name+' / '+r['model'] for r in rows])
        if i<0:return
        backup=rows[i]['path']
        if not rows[i]['cid_present']:raise RuntimeError('此旧备份缺少 eMMC CID 或经核对的历史身份补充，不能由插件还原')
    args=['probe','--action',action]
    if backup:args+=['--backup',backup]
    progress=xbmcgui.DialogProgress();progress.create('eMMC 检查',operation_message('核对机型、分区、启动介质和空间…'))
    try:report=call(*args)
    finally:progress.close()
    android_gib=None
    if action=='install':
        choices=report['capacity_choices']
        labels=['Android 用户数据 %d GiB / CE 数据 %.2f GiB' % (r['android_gib'],r['ce_storage_bytes']/1024**3) for r in choices]
        default=next(i for i,r in enumerate(choices) if r['android_gib']==report['android_gib'])
        selected=dialog.select('选择 Android 用户数据空间（不含系统；其余分给 CE）',labels,preselect=default)
        if selected<0:return
        android_gib=choices[selected]['android_gib']
        report['ce_storage_bytes']=choices[selected]['ce_storage_bytes']
    model=report['android_models'][0]
    summary='设备：%s\neMMC：%.1f GiB\n'%(model,report['emmc_bytes']/1024**3)
    if action=='backup':
        warning='将完整备份 eMMC 用户区和 boot0/boot1，并读回校验。不会修改 eMMC，也不需要重启。备份不包含 RPMB/eFuse，不是线刷包。需保持供电和外置盘连接。'
        button='开始备份'
    elif action=='repair':
        rows=report['ota_repair']['partitions'];boot,data,android=rows[-3:]
        warning=('适用于 Android OTA 后分区表回到原厂布局，但原 CE 文件系统仍完整的情况。将按现场数据恢复分区映射，不依赖旧安装记录。\n'
            '只写 DTB/MPT，不格式化、不主动重置 Android，不修改启动环境或 A/B 槽位。不能保证已损坏的数据可恢复，也不解决再次 OTA 的兼容性。\n'
            'CE 启动区 %.2f GiB，CE 数据区 %.2f GiB，Android 用户区 %.2f GiB。\n'
            '本操作不做整盘备份，仅保存修复前的分区元数据、env 和 misc。可能造成数据丢失或无法启动，插件作者不对修复造成的任何损失负责。\n'
            '完成后必须保留外置启动盘重启检查；重启前不要安装、卸载或再次修复。') % (boot['size']/1024**3,data['size']/1024**3,android['size']/1024**3)
        button='同意并修复布局'
    elif action=='restore':
        warning=('将用所选备份覆盖当前 CE、Android、用户数据和 boot0/boot1，当前数据不会保留；仅恢复备份时的内容，不保证恢复加密数据。\n'
            '安装/移除双系统会重置 Android。插件作者不对刷双系统或备份还原造成的任何损失负责。\n'
            '本次还原不会自动备份当前数据。过程中不要断电，完成后需要重启。\n所选备份：'+Path(backup).name)
        button='同意并还原'
    else:
        warning=('此操作会重置 Android，清空应用、账号和用户文件。'+('移除也会清除内置 CE。' if action=='remove' else '')+'\n'
            '可能造成数据丢失、无法启动或设备损坏。插件作者不对刷双系统造成的任何损失负责。\n'
            '本操作不会自动备份。如需保留当前状态，请取消并先选择“完整备份 eMMC”。请先停止播放、媒体库同步、扫描和刮削。操作期间不要退出 Kodi、断电或拔盘。完成后需关机拔下启动盘再开机。')
        if action=='install':warning+='\nAndroid 用户数据 %d GiB（不含系统分区）；CE 启动区 1 GiB，CE 数据区约 %.2f GiB。容量已扣除分区间隔。' % (android_gib,report['ce_storage_bytes']/1024**3)
        button='同意并'+('安装' if action=='install' else '移除')
    from_ng = any(str(report['os_release'].get(k,'')).startswith('Amlogic-ng') for k in ('DISTRO_DEVICE','COREELEC_DEVICE','COREELEC_ARCH','LIBREELEC_ARCH'))
    if from_ng:warning+='\nNG 双系统支持为测试功能，尚未完成内置启动验证。'
    warning+='\n开始前请先停止播放、媒体库同步、扫描和刮削。\n'+OPERATION_NOTICE
    if not dialog.yesno(TITLES[action],summary+warning,nolabel='取消',yeslabel=button):return
    args=['submit',action]
    if action=='repair':args+=['--accept-risk']
    elif action!='backup':args+=['--reset-android','--accept-risk']
    if backup:args+=['--backup',backup]
    if android_gib is not None:args+=['--android-gib',str(android_gib)]
    result=call(*args)
    run_foreground(result,TITLES[action])
