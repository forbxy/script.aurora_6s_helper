# 极光 4 Pro / 6S 助手

为腾讯极光 4 Pro（A4111）和 6S（A4112）提供无线修复与 RGB 灯条控制。
支持 6S、4 Pro RTL8852 和 4 Pro AP6275P，适用于 CoreELEC Amlogic-ng 21.x、Amlogic-no 22.x。

## 插件功能

### Wi-Fi、蓝牙与遥控器修复

- 自动识别 NG / NO 分支，优先读取原厂 Android 属性区分 A4111 / A4112。
- A4111 根据 S905X4 芯片修订号选择无线版本：rev A/B/C 为 AP6275P，rev D 为 RTL8852；A4112 使用 RTL8852。PCIe 已枚举时使用实际芯片 ID 交叉核对，未枚举时也能读取 CPU 修订号。
- 支持手动确认 4 Pro / 6S，适用于使用通用或 Ugoos X4 等设备树、且无法取得原厂机型的情况。
- RTL8852 NG 安装 Wi-Fi、灯条驱动并配置独立的开机启动服务，驱动加载不依赖 Kodi。
- RTL8852 NO 提供蓝牙配置；所有 NO 机型/无线版本均提供 Zidoo V12 输入识别修复，NG 不安装这条规则。
- AP6275P NG 使用系统自带的博通 Wi-Fi/蓝牙支持，安装专用 DTB 与独立灯条驱动服务；迁移时备份并清理旧 Realtek NG 驱动文件、配置和强制启动项。
- AP6275P NO 安装对应 DTB 及 V12 识别规则，使用 CE 自带的博通 Wi-Fi/蓝牙驱动和固件。
- 安装前检查兼容性并备份原文件，支持恢复；部署失败时尝试自动回退。

### RGB 灯条控制

- 分别设置常规状态和播放状态的颜色、亮度及效果。
- 支持常亮、呼吸和关闭，呼吸提供快、中、慢三档速度。
- 支持 Kodi 原生色块选色，也可精确输入 RGB 十六进制色值。
- 支持硬件平滑呼吸；可关闭平滑优先选项，以保留独立 RGB 亮度档位。
- 开机后自动应用保存的设置，播放音频或视频时切换灯光，停止播放后恢复常规状态。
- 保存设置后按当前播放状态立即应用，支持查看当前灯光状态。

## 驱动源码来源

| 组件 | 源码来源 |
| --- | --- |
| RTL8852BE Wi-Fi 驱动及内存模块（`8852be.ko`、`rtkm.ko`） | [CoreELEC/RTL8852BE-aml](https://github.com/CoreELEC/RTL8852BE-aml/tree/5da5adca92ac38a6c3a782147d45e48da577dce3) |
| TCA6507 灯条驱动（`leds-tca6507.ko`） | [CoreELEC/linux-amlogic — leds-tca6507.c](https://github.com/CoreELEC/linux-amlogic/blob/50c8e8154c5aba3c7cd8438d04dd5fdd51733e41/drivers/leds/leds-tca6507.c) |
| Realtek 蓝牙配置（`rtl8852bs_config`） | 基于 [CoreELEC/rtkbt-firmware-aml](https://github.com/CoreELEC/rtkbt-firmware-aml) 的配置进行修复 |
| AP6275P / BCM43752 Wi-Fi（NG/NO 系统自带博通驱动，插件不附带模块） | [CoreELEC/ap6xxx-aml](https://github.com/CoreELEC/ap6xxx-aml/tree/f0130717e2cdfc0d339a10e5b430ef15ee6b0c29) |

插件打包的 NG 驱动模块编译自上述固定提交。蓝牙使用 CoreELEC 自带的驱动和启动服务，RTL8852 使用插件提供的修复配置，AP6275P 使用系统博通固件。

## 修复数据目录

数据按 `resources/payload/{ng,no}/{6s,4pro}` 整理；4 Pro 再分为 `ap6275p` 和 `rtl8852`。
已收录 6S NG/NO、4 Pro RTL8852 NG/NO，以及 4 Pro AP6275P NG/NO。
4 Pro RTL8852 采用与 6S 相同的修复方案，DTB 仅更改机型名称和标识；该变体尚未进行实机验证。
详细文件布局见 [payload 说明](resources/payload/README.md)。
