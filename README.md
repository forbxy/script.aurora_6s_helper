# 极光 6S 助手

为腾讯极光 6S（A4112）提供无线修复与 RGB 灯条控制，适用于 CoreELEC Amlogic-ng 21.x 和 Amlogic-no 22.x。

## 插件功能

### Wi-Fi、蓝牙与遥控器修复

- 自动识别 NG / NO 分支，安装对应的极光 6S 设备树和蓝牙配置。
- 支持手动确认机型，适用于实物为 6S、但使用通用或 Ugoos X4 等设备树的情况。
- NG 安装 Wi-Fi、灯条驱动并配置独立的开机启动服务，驱动加载不依赖 Kodi。
- NO 提供 Zidoo V12 遥控器的输入识别修复；NG 不安装这条规则。
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

插件打包的 NG 驱动模块编译自上述固定提交。蓝牙使用 CoreELEC 自带的驱动和启动服务，插件提供修复后的配置文件。
