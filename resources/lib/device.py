"""Identify the supported board/build without relying on Kodi."""
import os
import gzip
from pathlib import Path
import platform
import shlex

DT = Path('/proc/device-tree')
RELEASE = Path('/etc/os-release')
NG_RELEASE = '4.9.269'
KERNEL_CONFIG = Path('/proc/config.gz')
NG_FLAGS = ('CONFIG_ARM64=y', 'CONFIG_SMP=y', 'CONFIG_PREEMPT=y', 'CONFIG_MODULE_UNLOAD=y', 'CONFIG_MODVERSIONS=y')
DT_IDS = {'no': 'sc2_s905x4_tencent_aurora_6s',
          'ng': 'sc2_s905x4_tencent_aurora_6s_ng'}


def text_property(name):
    p = DT / name
    return p.read_bytes().rstrip(b'\0').decode(errors='replace') if p.exists() else ''


def check_ng_kernel():
    # A build date is not a module ABI. insmod still enforces vermagic and CRCs.
    if platform.release() != NG_RELEASE:
        raise RuntimeError('NG 驱动要求 4.9.269 / aarch64 内核')
    # NG has 32-bit userspace: Python uname may report armv7l on an ARM64 kernel.
    with gzip.open(KERNEL_CONFIG, 'rt') as source:
        config = set(source.read().splitlines())
    if not set(NG_FLAGS).issubset(config):
        raise RuntimeError('NG 内核模块配置不匹配，需要 ARM64/SMP/PREEMPT/MODULE_UNLOAD/MODVERSIONS')


def detect(confirmed_6s=False, allow_unidentified=False, check_kernel=True):
    data = {}
    for line in RELEASE.read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            values = shlex.split(v)
            data[k] = values[0] if len(values) == 1 else v
    if data.get('ID') != 'coreelec':
        raise RuntimeError('仅支持 CoreELEC 系统')
    variants = {data[k] for k in ('DISTRO_DEVICE', 'COREELEC_DEVICE') if data.get(k)}
    for key in ('COREELEC_ARCH', 'LIBREELEC_ARCH'):
        if data.get(key):
            variants.add(data[key].split('.')[0])
    if len(variants) != 1 or next(iter(variants)) not in ('Amlogic-ng', 'Amlogic-no'):
        raise RuntimeError('无法确定 Amlogic-ng / Amlogic-no 分支，拒绝部署')
    branch = next(iter(variants)).split('-')[-1]
    version = data.get('VERSION_ID', '')
    if branch == 'ng' and version.split('.')[0] != '21':
        raise RuntimeError('NG 修复包支持 CoreELEC 21.x，请勿用于其他大版本')
    if branch == 'no' and version.split('.')[0] != '22':
        raise RuntimeError('NO 修复包仅验证 CoreELEC 22')
    if branch == 'ng' and check_kernel:
        check_ng_kernel()
    compatible = text_property('compatible').replace(' ', '').split('\0')
    if 'amlogic,sc2' not in compatible:
        raise RuntimeError('硬件平台不匹配：需要极光 6S 的 SC2 平台')
    ident = text_property('coreelec-dt-id')
    model = text_property('model')
    known = ident in DT_IDS.values() or model in (
        'Tencent Aurora Box 6S', 'Tencent Aurora Box 6S (CoreELEC NG)',
        'Tencent Aurora Box 6S (A4112)')
    if not known and not confirmed_6s and not allow_unidentified:
        raise RuntimeError('当前设备树未识别为极光 6S，需先明确确认实物为极光 6S（A4112）。请从插件的修复硬件入口确认机型')
    if os.geteuid() != 0:
        raise RuntimeError('需要 CoreELEC 的 root 权限')
    return dict(branch=branch, version=version, model=model, dt_id=ident,
                board_verified=known, confirmation_required=not known)
