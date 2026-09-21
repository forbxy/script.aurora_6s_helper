import json
from pathlib import Path
import sys

import xbmc
import xbmcaddon
import xbmcgui

sys.path.insert(0, str(Path(__file__).parent / 'resources/lib'))
from config import ADDON_ID, parse_color
import repair


def notify_service():
    xbmc.executebuiltin('NotifyAll(%s,aurora.apply)' % ADDON_ID)


def main():
    dialog = xbmcgui.Dialog()
    addon = xbmcaddon.Addon(ADDON_ID)
    choice = sys.argv[1] if len(sys.argv) > 1 else ''
    if not choice:
        selected = dialog.select('极光 6S 助手', ['修复 Wi-Fi / 蓝牙 / V12 遥控器', '设置灯条（常规 / 播放）'])
        if selected < 0:
            return
        choice = ('repair', 'lights')[selected]
        if choice == 'lights':
            action = dialog.select('灯条设置', ['色盘选色与灯光效果', '精确输入颜色值'])
            if action < 0:
                return
            if action == 1:
                choice = 'custom-color'
    if choice == 'custom-color':
        scene = dialog.select('精确输入颜色', ['常规状态', '播放状态'])
        if scene < 0:
            return
        key = ('normal', 'playback')[scene] + '.color'
        old = addon.getSetting(key) or 'FF0000FF'
        value = dialog.input('输入六位颜色值 RRGGBB，例如 0080FF', defaultt=old[-6:])
        if not value:
            return
        rgb = parse_color(value.strip())
        addon.setSetting(key, 'FF' + ''.join('%02X' % c for c in rgb))
        notify_service()
        dialog.notification('极光 6S 灯条', '颜色已保存，并按当前播放状态应用')
        return
    if choice == 'lights':
        addon.openSettings()
        notify_service()
        return
    if choice == 'apply':
        notify_service()
        dialog.notification('极光 6S 灯条', '已请求按当前播放状态应用保存的设置')
        return
    if choice == 'status':
        p = Path('/run/aurora6s-led-status.json')
        if not p.exists():
            dialog.ok('灯条状态', '服务尚未运行，请启用插件或重启 Kodi。')
        else:
            info = json.loads(p.read_text(encoding='utf-8'))
            if 'error' in info:
                dialog.ok('灯条状态', info['error'])
            else:
                profile = info['profile']
                scene = '播放' if profile['name'] == 'playback' else '常规'
                engine = {'disabled': '已停用', 'hardware': '硬件控制', 'software': '软件呼吸'}[info['backend']]
                dialog.ok('灯条状态', '%s / %s\nRGB %s，亮度 %s%%\n%s' %
                          (scene, profile['mode'], profile['rgb'], profile['brightness'], engine))
        return
    if choice != 'repair':
        return
    result = repair.check()
    if not dialog.yesno('极光 6S 硬件修复', result + '\n\n仅用于腾讯极光 6S（A4112）。将备份并更新 DTB、蓝牙配置及 V12 规则，完成后需要重启。确认此盒子为 6S 并安装？',
                        nolabel='取消', yeslabel='备份并安装'):
        return
    progress = xbmcgui.DialogProgressBG()
    progress.create('极光 6S 硬件修复', '正在备份并部署…')
    try:
        backup = repair.install()
    finally:
        progress.close()
    if dialog.yesno('修复已安装', '已核对文件。重启后加载新配置。\n备份：' + backup,
                    nolabel='稍后重启', yeslabel='立即重启'):
        xbmc.executebuiltin('Reboot')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        xbmc.log('[Aurora6S] ' + str(exc), xbmc.LOGERROR)
        xbmcgui.Dialog().ok('极光 6S 助手', str(exc))
