"""Standalone, backed-up repair installer. Never loads drivers through Kodi."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from device import detect

PAYLOAD = Path(__file__).resolve().parents[1] / 'payload'
BACKUPS = Path('/storage/ce-fix-backup')
LOCK = Path('/run/aurora6s-repair.lock')
SYSTEMD = Path('/storage/.config/system.d')
COMMON = {'rtl8852bs_config': '/storage/.config/firmware/rtlbt/rtl8852bs_config'}
NO = {'dtb.img': '/flash/dtb.img',
      '99-zidoo-v12-keyboard.rules': '/storage/.config/udev.rules.d/99-zidoo-v12-keyboard.rules'}
NG = {'ng/dtb.img': '/flash/dtb.img'}
for _name in ('8852be.ko', 'rtkm.ko', 'leds-tca6507.ko', 'load-drivers.sh'):
    NG['ng/' + _name] = '/storage/.config/aurora6s-ng/' + _name
for _name in ('aurora6s-ng-wifi.service', 'aurora6s-ng-led.service'):
    NG['ng/' + _name] = str(SYSTEMD / _name)
UNITS = ('aurora6s-ng-wifi.service', 'aurora6s-ng-led.service', 'rtkbt-firmware-aml.service')


def targets(branch):
    return dict(COMMON, **(NG if branch == 'ng' else NO))


def links(branch):
    if branch != 'ng':
        return {}
    return {name: SYSTEMD / 'multi-user.target.wants' / name for name in UNITS}


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


def set_link(path, value):
    path = Path(path)
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.aurora-', dir=str(path.parent)) as d:
        temp = Path(d) / 'link'
        temp.symlink_to(value)
        os.replace(temp, path)


def verify_payload():
    expected = json.loads((PAYLOAD / 'hashes.json').read_text(encoding='utf-8'))
    if set(expected) != set(COMMON) | set(NO) | set(NG):
        raise RuntimeError('修复包清单不完整')
    for name, digest in expected.items():
        if sha(PAYLOAD / name) != digest:
            raise RuntimeError('修复包校验失败：' + name)


def flash_is_ro():
    for line in Path('/proc/mounts').read_text().splitlines():
        fields = line.split()
        if fields[1] == '/flash':
            return 'ro' in fields[3].split(',')
    raise RuntimeError('/flash 未挂载')


def check(confirmed_6s=False, allow_unidentified=False):
    profile = detect(confirmed_6s=confirmed_6s, allow_unidentified=allow_unidentified)
    verify_payload()
    flash_is_ro()
    if not Path('/flash/dtb.img').is_file():
        raise RuntimeError('未找到外置 CE 的 /flash/dtb.img')
    if profile['branch'] == 'ng':
        if not Path('/usr/lib/systemd/system/rtkbt-firmware-aml.service').is_file():
            raise RuntimeError('缺少 CE 的 Realtek 蓝牙启动服务')
        config = Path('/flash/config.ini').read_text()
        # Experimental boot overrides must be removed deliberately, not silently carried over.
        if any(s in config for s in ('amlogic_pcie_init', 'wifi_dummy_init', 'systemd.mask=aurora6s-ng-wifi')):
            raise RuntimeError('config.ini 仍有 PCIe/Wi-Fi 诊断启动参数，请先恢复正常启动配置')
    profile['files'] = {name: Path(target).is_file() and sha(target) == sha(PAYLOAD / name)
                        for name, target in targets(profile['branch']).items()}
    return profile


def snapshot(path, backup):
    path = Path(path)
    if path.is_symlink():
        return {'link': os.readlink(path)}
    if path.exists():
        atomic_copy(path, backup)
        return {'sha256': sha(backup), 'mode': path.stat().st_mode & 0o777}
    return None


def restore_file(path, saved, source):
    path = Path(path)
    if saved is None:
        path.unlink(missing_ok=True)
    elif 'link' in saved:
        set_link(path, saved['link'])
    else:
        atomic_copy(source, path)
        os.chmod(path, saved['mode'])
        if sha(path) != saved['sha256']:
            raise RuntimeError('恢复校验失败：' + str(path))


def restore(backup):
    backup = Path(backup)
    manifest = json.loads((backup / 'manifest.json').read_text())
    branch = manifest['branch']
    if branch not in ('ng', 'no') or set(manifest['files']) != set(targets(branch)) or set(manifest['links']) != set(links(branch)):
        raise RuntimeError('备份清单不匹配')
    for name, saved in manifest['files'].items():
        if saved and 'sha256' in saved and sha(backup / name) != saved['sha256']:
            raise RuntimeError('备份校验失败：' + name)
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, target in targets(branch).items():
            restore_file(target, manifest['files'][name], backup / name)
        for name, path in links(branch).items():
            set_link(path, manifest['links'][name])
        run('systemctl', 'daemon-reload')
        if manifest['keepalive_enabled'] in ('enabled', 'enabled-runtime'):
            args = ['systemctl', 'enable']
            if manifest['keepalive_enabled'] == 'enabled-runtime':
                args.append('--runtime')
            run(*(args + ['bt-uart-keepalive.service']))
        if manifest['keepalive_active'] == 'active':
            run('systemctl', 'start', 'bt-uart-keepalive.service')
        if branch == 'no':
            run('udevadm', 'control', '--reload-rules')
        os.sync()
    finally:
        if was_ro:
            run('mount', '-o', 'remount,ro', '/flash')


def install(confirmed_6s=False):
    profile = check(confirmed_6s=confirmed_6s)
    branch = profile['branch']
    # Reject non-symlink wants entries before any modification.
    original_links = {}
    for name, path in links(branch).items():
        if path.exists() and not path.is_symlink():
            raise RuntimeError('启动链接不是符号链接：' + str(path))
        original_links[name] = os.readlink(path) if path.is_symlink() else None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='aurora-addon-', dir=str(BACKUPS)))
    manifest = dict(branch=branch, files={}, links=original_links,
                    keepalive_enabled=run('systemctl', 'is-enabled', 'bt-uart-keepalive.service', check=False).stdout.strip(),
                    keepalive_active=run('systemctl', 'is-active', 'bt-uart-keepalive.service', check=False).stdout.strip())
    for name, target in targets(branch).items():
        manifest['files'][name] = snapshot(target, backup / name)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    shutil.copy2(__file__, backup / 'repair.py')
    shutil.copy2(Path(__file__).with_name('device.py'), backup / 'device.py')
    (backup / 'restore.sh').write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec /usr/bin/python3 repair.py --restore "$PWD"\n')
    os.sync()
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, target in targets(branch).items():
            atomic_copy(PAYLOAD / name, target)
            if sha(target) != sha(PAYLOAD / name):
                raise RuntimeError('写入校验失败：' + name)
        for name, path in links(branch).items():
            target = '/usr/lib/systemd/system/' + name if name == 'rtkbt-firmware-aml.service' else str(SYSTEMD / name)
            set_link(path, target)
        run('systemctl', 'daemon-reload')
        load = run('systemctl', 'show', '-p', 'LoadState', '--value', 'bt-uart-keepalive.service').stdout.strip()
        if load and load != 'not-found':
            run('systemctl', 'disable', '--now', 'bt-uart-keepalive.service')
        if branch == 'no':
            run('udevadm', 'control', '--reload-rules')
        # Drivers are intentionally not started against the old live DTB.
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


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--install', action='store_true')
    action.add_argument('--restore', metavar='BACKUP')
    parser.add_argument('--confirm-6s', action='store_true')
    args = parser.parse_args()
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.check:
            print(json.dumps(check(allow_unidentified=True), ensure_ascii=False))
        elif args.install:
            print(json.dumps({'backup': install(args.confirm_6s)}, ensure_ascii=False))
        else:
            profile = detect(confirmed_6s=args.confirm_6s, check_kernel=False)
            manifest = json.loads((Path(args.restore) / 'manifest.json').read_text())
            if profile['branch'] != manifest['branch']:
                raise RuntimeError('备份所属分支与当前系统不同')
            restore(args.restore)
            print('原文件与启动链接已恢复；请重启盒子。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(str(exc))
