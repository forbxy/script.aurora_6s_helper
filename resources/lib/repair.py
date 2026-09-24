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

from device import detect, payload_key, PROFILE_IDS

PAYLOAD = Path(__file__).resolve().parents[1] / 'payload'
BACKUPS = Path('/storage/ce-fix-backup')
LOCK = Path('/run/aurora6s-repair.lock')
SYSTEMD = Path('/storage/.config/system.d')
NO = {'no/6s/dtb.img': '/flash/dtb.img',
      'no/6s/rtl8852bs_config': '/storage/.config/firmware/rtlbt/rtl8852bs_config',
      'no/6s/99-zidoo-v12-keyboard.rules': '/storage/.config/udev.rules.d/99-zidoo-v12-keyboard.rules'}
NG = {'ng/6s/dtb.img': '/flash/dtb.img',
      'ng/6s/rtl8852bs_config': '/storage/.config/firmware/rtlbt/rtl8852bs_config'}
for _name in ('8852be.ko', 'rtkm.ko', 'leds-tca6507.ko', 'load-drivers.sh'):
    NG['ng/6s/' + _name] = '/storage/.config/aurora6s-ng/' + _name
for _name in ('aurora6s-ng-wifi.service', 'aurora6s-ng-led.service'):
    NG['ng/6s/' + _name] = str(SYSTEMD / _name)
UNITS = ('aurora6s-ng-wifi.service', 'aurora6s-ng-led.service', 'rtkbt-firmware-aml.service')


def targets(branch, board='6s', chip='rtl8852'):
    prefix = payload_key(branch, board, chip)
    if chip == 'ap6275p':
        allowed = ('dtb.img', 'leds-tca6507.ko', 'load-drivers.sh', 'aurora6s-ng-led.service') if branch == 'ng' else ('dtb.img', '99-zidoo-v12-keyboard.rules')
        base = NG if branch == 'ng' else NO
        return {prefix + '/' + Path(name).name: target for name, target in base.items()
                if Path(name).name in allowed}
    base = NG if branch == 'ng' else NO
    return {prefix + '/' + Path(name).name: target for name, target in base.items()}


def profile_targets(profile):
    return targets(profile['branch'], profile.get('board', '6s'), profile.get('chip', 'rtl8852'))


def cleanup_targets(branch, chip):
    """Only remove known previous Realtek deployment files, never whole directories."""
    if (branch, chip) != ('ng', 'ap6275p'):
        return {}
    return {'cleanup/' + Path(name).name: target for name, target in NG.items()
            if Path(name).name in ('8852be.ko', 'rtkm.ko', 'rtl8852bs_config', 'aurora6s-ng-wifi.service')}


def desired_link(branch, chip, name):
    if branch == 'ng' and chip == 'ap6275p' and name != 'aurora6s-ng-led.service':
        return None
    return '/usr/lib/systemd/system/' + name if name == 'rtkbt-firmware-aml.service' else str(SYSTEMD / name)


def backup_targets(manifest):
    """Keep pre-layout backups restorable without trusting arbitrary paths."""
    branch = manifest['branch']
    version = manifest.get('format', 1)
    if version in (4, 5):
        return targets(branch, manifest['board'], manifest['chip'])
    if version == 3:
        saved = targets(branch, manifest['board'], manifest['chip'])
        # 1.3.0 Broadcom backups predate the shared NO input fix.
        if manifest['chip'] == 'ap6275p':
            saved = {name: target for name, target in saved.items() if name.endswith('/dtb.img')}
        return saved
    current = targets(branch)
    if version == 2:
        return current
    if version != 1:
        raise RuntimeError('未知备份格式')
    legacy = {}
    for name, target in current.items():
        filename = Path(name).name
        key = ('ng/' + filename if branch == 'ng' and filename != 'rtl8852bs_config'
               else filename)
        legacy[key] = target
    return legacy


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
    files = set()
    for key in PROFILE_IDS:
        parts = key.split('/')
        files.update(targets(parts[0], parts[1], parts[2] if len(parts) == 3 else 'rtl8852'))
    if set(expected) != files:
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


def check(confirmed_6s=False, allow_unidentified=False, confirmed_board=None):
    profile = detect(confirmed_6s=confirmed_6s, allow_unidentified=allow_unidentified,
                     confirmed_board=confirmed_board)
    verify_payload()
    flash_is_ro()
    if not Path('/flash/dtb.img').is_file():
        raise RuntimeError('未找到外置 CE 的 /flash/dtb.img')
    if not profile['board']:
        profile['files'] = {}
        return profile
    if profile['branch'] == 'ng':
        unit = 'brcmfmac_sdio-firmware-aml.service' if profile['chip'] == 'ap6275p' else 'rtkbt-firmware-aml.service'
        if not Path('/usr/lib/systemd/system', unit).is_file():
            raise RuntimeError('缺少 CE 蓝牙启动服务：' + unit)
        if profile['chip'] == 'ap6275p':
            for required in ('/usr/lib/udev/rules.d/80-brcmfmac_pci.rules', '/lib/firmware/brcm/BCM4362A2.hcd'):
                if not Path(required).is_file():
                    raise RuntimeError('缺少 CE 博通支持文件：' + required)
        config = Path('/flash/config.ini').read_text()
        # Experimental boot overrides must be removed deliberately, not silently carried over.
        if any(s in config for s in ('amlogic_pcie_init', 'wifi_dummy_init', 'systemd.mask=aurora6s-ng-wifi')):
            raise RuntimeError('config.ini 仍有 PCIe/Wi-Fi 诊断启动参数，请先恢复正常启动配置')
    profile['files'] = {name: Path(target).is_file() and sha(target) == sha(PAYLOAD / name)
                        for name, target in profile_targets(profile).items()}
    profile['cleanup'] = [target for target in cleanup_targets(profile['branch'], profile['chip']).values()
                          if os.path.lexists(target)]
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
    saved_targets = backup_targets(manifest)
    removed = cleanup_targets(branch, manifest['chip']) if manifest.get('format') == 5 else {}
    if set(manifest.get('removed', {})) != set(removed):
        raise RuntimeError('清理备份清单不匹配')
    if set(manifest['files']) != set(saved_targets) or set(manifest['links']) != set(links(branch)):
        raise RuntimeError('备份清单不匹配')
    for name, saved in manifest['files'].items():
        if saved and 'sha256' in saved and sha(backup / name) != saved['sha256']:
            raise RuntimeError('备份校验失败：' + name)
    for name, saved in manifest.get('removed', {}).items():
        if saved and 'sha256' in saved and sha(backup / name) != saved['sha256']:
            raise RuntimeError('清理备份校验失败：' + name)
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, target in saved_targets.items():
            restore_file(target, manifest['files'][name], backup / name)
        for name, target in removed.items():
            restore_file(target, manifest['removed'][name], backup / name)
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


def install(confirmed_6s=False, confirmed_board=None):
    profile = check(confirmed_6s=confirmed_6s, confirmed_board=confirmed_board)
    branch = profile['branch']
    board, chip = profile.get('board', '6s'), profile.get('chip', 'rtl8852')
    selected_targets = profile_targets(profile)
    removed = cleanup_targets(branch, chip)
    manage_keepalive = chip == 'rtl8852' or bool(removed)
    # Reject non-symlink wants entries before any modification.
    original_links = {}
    for name, path in links(branch).items():
        if path.exists() and not path.is_symlink():
            raise RuntimeError('启动链接不是符号链接：' + str(path))
        original_links[name] = os.readlink(path) if path.is_symlink() else None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='aurora-addon-', dir=str(BACKUPS)))
    manifest = dict(format=5, branch=branch, board=board, chip=chip, files={}, removed={}, links=original_links,
                    keepalive_enabled=run('systemctl', 'is-enabled', 'bt-uart-keepalive.service', check=False).stdout.strip() if manage_keepalive else 'disabled',
                    keepalive_active=run('systemctl', 'is-active', 'bt-uart-keepalive.service', check=False).stdout.strip() if manage_keepalive else 'inactive')
    for name, target in selected_targets.items():
        manifest['files'][name] = snapshot(target, backup / name)
    for name, target in removed.items():
        manifest['removed'][name] = snapshot(target, backup / name)
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    shutil.copy2(__file__, backup / 'repair.py')
    shutil.copy2(Path(__file__).with_name('device.py'), backup / 'device.py')
    (backup / 'restore.sh').write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec /usr/bin/python3 repair.py --restore "$PWD"\n')
    os.sync()
    was_ro = flash_is_ro()
    run('mount', '-o', 'remount,rw', '/flash')
    try:
        for name, target in selected_targets.items():
            atomic_copy(PAYLOAD / name, target)
            if sha(target) != sha(PAYLOAD / name):
                raise RuntimeError('写入校验失败：' + name)
        for name, path in links(branch).items():
            set_link(path, desired_link(branch, chip, name))
        for target in removed.values():
            Path(target).unlink(missing_ok=True)
        run('systemctl', 'daemon-reload')
        if manage_keepalive:
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
    parser.add_argument('--confirm-board', choices=('6s', '4pro'))
    args = parser.parse_args()
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.check:
            print(json.dumps(check(confirmed_6s=args.confirm_6s, confirmed_board=args.confirm_board,
                                   allow_unidentified=True), ensure_ascii=False))
        elif args.install:
            print(json.dumps({'backup': install(args.confirm_6s, args.confirm_board)}, ensure_ascii=False))
        else:
            profile = detect(confirmed_6s=args.confirm_6s, confirmed_board=args.confirm_board, check_kernel=False)
            manifest = json.loads((Path(args.restore) / 'manifest.json').read_text())
            if profile['branch'] != manifest['branch']:
                raise RuntimeError('备份所属分支与当前系统不同')
            if (profile['board'], profile['chip']) != (manifest.get('board', '6s'), manifest.get('chip', 'rtl8852')):
                raise RuntimeError('备份所属机型/芯片与当前设备不同')
            restore(args.restore)
            print('原文件与启动链接已恢复；请重启盒子。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(str(exc))
