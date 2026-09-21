"""Backed-up atomic deployment; standalone restore works after add-on removal."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from hardware import check_platform

PAYLOAD = Path(__file__).resolve().parents[1] / 'payload'
TARGETS = {
    'dtb.img': '/flash/dtb.img',
    'rtl8852bs_config': '/storage/.config/firmware/rtlbt/rtl8852bs_config',
    '99-zidoo-v12-keyboard.rules': '/storage/.config/udev.rules.d/99-zidoo-v12-keyboard.rules',
}
BACKUPS = Path('/storage/ce-fix-backup')


def run(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=30)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.aurora-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as out, open(source, 'rb') as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o644)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def verify_payload():
    expected = json.loads((PAYLOAD / 'hashes.json').read_text(encoding='utf-8'))
    if set(expected) != set(TARGETS):
        raise RuntimeError('修复包清单不完整')
    for name, digest in expected.items():
        if sha(PAYLOAD / name) != digest:
            raise RuntimeError('修复包校验失败：' + name)


def flash_is_ro():
    for line in Path('/proc/mounts').read_text(encoding='utf-8').splitlines():
        fields = line.split()
        if fields[1] == '/flash':
            return 'ro' in fields[3].split(',')
    raise RuntimeError('/flash 未挂载')


def check():
    check_platform()
    verify_payload()
    flash_is_ro()
    if not Path('/flash/dtb.img').is_file():
        raise RuntimeError('未找到外置 CE 的 /flash/dtb.img')
    return '\n'.join(name + ('：已匹配' if Path(target).is_file() and sha(target) == sha(PAYLOAD / name) else '：待更新')
                     for name, target in TARGETS.items())


def restore(backup):
    backup = Path(backup)
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    # Never accept arbitrary write destinations from a manifest.
    if set(manifest['files']) != set(TARGETS):
        raise RuntimeError('备份清单不匹配')
    for name, digest in manifest['files'].items():
        if digest is not None and sha(backup / name) != digest:
            raise RuntimeError('备份校验失败：' + name)
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, digest in manifest['files'].items():
            if digest is None:
                Path(TARGETS[name]).unlink(missing_ok=True)
            else:
                atomic_copy(backup / name, TARGETS[name])
                if sha(TARGETS[name]) != digest:
                    raise RuntimeError('恢复校验失败：' + name)
        if manifest['keepalive_enabled'] in ('enabled', 'enabled-runtime'):
            args = ['systemctl', 'enable']
            if manifest['keepalive_enabled'] == 'enabled-runtime':
                args.append('--runtime')
            run(*(args + ['bt-uart-keepalive.service']))
        if manifest['keepalive_active'] == 'active':
            run('systemctl', 'start', 'bt-uart-keepalive.service')
        run('udevadm', 'control', '--reload-rules')
        os.sync()
    finally:
        if was_ro:
            run('mount', '-o', 'remount,ro', '/flash')


def install():
    check()
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='aurora-addon-', dir=str(BACKUPS)))
    manifest = dict(files={}, keepalive_enabled=run('systemctl', 'is-enabled', 'bt-uart-keepalive.service', check=False).stdout.strip(),
                    keepalive_active=run('systemctl', 'is-active', 'bt-uart-keepalive.service', check=False).stdout.strip())
    for name, target in TARGETS.items():
        if Path(target).exists():
            atomic_copy(target, backup / name)
            manifest['files'][name] = sha(backup / name)
        else:
            manifest['files'][name] = None
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    shutil.copy2(__file__, backup / 'repair.py')
    shutil.copy2(Path(__file__).with_name('hardware.py'), backup / 'hardware.py')
    (backup / 'restore.sh').write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec /usr/bin/python3 repair.py --restore\n')
    os.sync()
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, target in TARGETS.items():
            atomic_copy(PAYLOAD / name, target)
            if sha(target) != sha(PAYLOAD / name):
                raise RuntimeError('写入校验失败：' + name)
        load = run('systemctl', 'show', '-p', 'LoadState', '--value', 'bt-uart-keepalive.service').stdout.strip()
        if load != 'not-found':
            run('systemctl', 'disable', '--now', 'bt-uart-keepalive.service')
        run('udevadm', 'control', '--reload-rules')
        os.sync()
    except Exception as exc:
        try:
            restore(backup)
        except Exception as rollback:
            raise RuntimeError('部署失败且自动恢复失败。备份：%s；%s；%s' % (backup, exc, rollback)) from exc
        raise RuntimeError('部署失败，已恢复原文件。备份：%s；%s' % (backup, exc)) from exc
    finally:
        if was_ro:
            run('mount', '-o', 'remount,ro', '/flash')
    return str(backup)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--restore']:
        raise SystemExit('Use: python3 repair.py --restore (from backup directory)')
    check_platform()
    restore(Path(__file__).resolve().parent)
    print('原文件已恢复；请重启盒子。')
