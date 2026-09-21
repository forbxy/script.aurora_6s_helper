# 极光 6S 助手

插件 ID：`script.aurora_6s_helper`。适用于腾讯极光 6S（A4112）的 CoreELEC 22 Amlogic-no；当前测试系统为 22.0-Piers_beta2。源码以工作区本目录为准，再打包和同步到盒子。

## 安装与使用

在 Kodi 的“插件 → 从 ZIP 文件安装”选择 `script.aurora_6s_helper-1.1.3.zip`，启用插件，然后在“程序插件”打开“极光 6S 助手”。主菜单有两个入口：

1. **修复 Wi-Fi / 蓝牙 / V12 遥控器**：核对目标文件，确认机型后备份并安装。完成后可立即重启或稍后重启。
2. **设置灯条（常规 / 播放）**：设置两套颜色、亮度、常亮、呼吸或关闭、呼吸速度。点击“确定”保存后按当前播放状态应用；取消不会保存修改。

灯光默认启用：常规为蓝色、50%亮度、常亮；播放为蓝色、15%亮度、常亮。效果选“关闭”只关闭当前场景的RGB输出，保留颜色和亮度；切回常亮/呼吸或切换到其他场景会恢复对应效果。它不同于停用整个灯控服务，不会释放芯片并恢复原厂灯光。亮度为0也表示关灯。颜色控件使用 Kodi 原生色块色盘：方向键选择，OK 确认，设置页显示色块预览。精确输入在插件主菜单的“设置灯条 → 精确输入颜色值”中，支持六位 RRGGBB，例如 `0000FF` 蓝、`00FFFF` 青。旧版本六位颜色会自动迁移为原生控件的八位 AARRGGBB，RGB保持不变，灯条亮度仍由独立亮度选项控制。

音频、视频播放均使用播放设置，暂停仍视为播放；停止或播放结束恢复常规。保存设置时读取实际播放状态，修改非当前场景不会强行预览该场景。Kodi 启动时服务也会检测是否已有播放并应用对应设置。

设置由 Kodi 持久保存到当前用户配置目录的 `addon_data/script.aurora_6s_helper/settings.xml`，默认用户路径为 `/storage/.kodi/userdata/addon_data/script.aurora_6s_helper/settings.xml`。本插件没有额外的自定义 JSON 偏好文件。选择插件设置中的“查看当前灯光状态”可查看实际场景、后端和错误。

## 硬件修复

仅安装三个文件：

| 打包文件 | 部署位置 |
| --- | --- |
| dtb.img | /flash/dtb.img |
| rtl8852bs_config | /storage/.config/firmware/rtlbt/rtl8852bs_config |
| 99-zidoo-v12-keyboard.rules | /storage/.config/udev.rules.d/99-zidoo-v12-keyboard.rules |

DTB 是原生 TCA6507 RGB 版本（1.1.2起默认纯蓝：红绿关闭、蓝色常亮），并包含 PCIe/Wi-Fi/蓝牙接线适配。蓝牙配置清除0x01be的bit1，替代以前的心跳保活；部署会停用旧 `bt-uart-keepalive.service`。V12规则保留键盘分类，去除错误的tablet标签，不改鼠标接口。

保留现有 `/flash/config.ini`，不替换其中的用户设置，不清除配对，不修改原厂 Android DTB/MPT或eMMC布局。插件每次启动不会重新刷写硬件修复。V12规则重载后，新建输入设备会使用新规则；完整修复统一在重启后生效。

每次安装先备份到 `/storage/ce-fix-backup/aurora-addon-*`，保存原文件、原文件是否存在、旧保活服务状态及哈希；文件用同目录临时文件和替换方式部署，异常时尝试自动回退，恢复原来的 /flash 只读状态。

手工恢复：在备份目录执行 `sh restore.sh`，然后重启。恢复脚本随备份保存，即使卸载插件仍可使用。卸载插件不会自动回退无线修复。

## 灯控实现与边界

- 使用本机 I2C3/0x45 TCA6507，P0/P1/P2对应红/绿/蓝。仅在运行中的设备树 ID 为本项目原生RGB版本时接管。
- 接管前保存通道状态并解绑内核LED驱动，使用普通 I2C_SLAVE，不强占仍被驱动使用的地址。一个文件锁保证只有一个服务拥有芯片。
- 保持 sys_led 对应芯片使能常开。使用本插件期间，请将 CE 原有“系统指示灯”菜单保持 on；其 heartbeat/off 会复位或关闭芯片。
- 常亮使用芯片三个亮度源。默认开启“呼吸优先平滑”，由芯片执行硬件呼吸；若 RGB 有三种不同的非零亮度，将最接近的两种合并为平均档位，颜色会有所变化，保存的原始 RGB 不变。关闭此开关时，最多两种亮度仍用硬件呼吸，三种亮度改用约16ms更新的软件呼吸。常亮不做合并。
- 芯片只有16档亮度，低亮度颜色会量化甚至熄灭；RGB输入并不代表显示器级色准，实际颜色受灯珠影响。软件呼吸也是16档，低亮度时可能更明显看到阶梯。16ms更新可减少快速呼吸时跳过档位，但不会增加芯片的亮度档位；进入软件呼吸从熄灭开始，避免先闪到峰值。
- 呼吸速度快/中/慢对应每段渐亮或渐暗约768/1536/3072ms，峰值保持128ms，熄灭256ms。硬件内部有两次相同的呼吸阶段；不是双闪。
- 服务监听 Kodi 设置和播放通知，并每250ms检查状态以防漏事件。硬件效果不持续写寄存器；软件呼吸仅在量化输出发生变化时写入。
- 停用灯控或正常禁用/卸载插件时恢复原驱动及接管前灯光。Kodi 正常退出也会恢复；下次启动重新应用保存配置。异常终止留下的接管状态保存在 `/run`，下次服务接管沿用；断电后由系统初始化。
- 插件仍需常驻监听播放事件。它不依赖新的 systemd 服务或 autostart.sh。

## 本地构建与检查

从工作区根目录执行：

```sh
python3 -m unittest discover -s tests/aurora6s -v
python3 work/build_aurora_addon.py
```

输出到 `output/addons/`，包含 ZIP 和 SHA256。ZIP 单一顶层目录为 `script.aurora_6s_helper`，不包含 Python 缓存、测试脚本、配对信息或个人数据。

测试记录见工作区 `logs/addon-aurora6s-20260921/`。升级时先停止旧插件服务，再同步本地版本并重新启用；不能在旧服务持有I2C时手工启动第二份 service.py。

## 参考与来源

- Kodi Service add-ons：https://kodi.wiki/view/Service_add-ons
- Kodi 设置格式：https://kodi.wiki/view/Add-on_settings_conversion
- TI TCA6507：https://www.ti.com/lit/ds/symlink/tca6507.pdf
- 修复资产来自本工作区 `output/aurora6s-ce22-working-20260921/` 和 `output/v12-input-fix/`，打包哈希见 resources/payload/hashes.json。
- Realtek 配置上游：https://github.com/CoreELEC/rtkbt-firmware-aml （配置文件沿用其上游授权；本插件未打包蓝牙固件程序）。
