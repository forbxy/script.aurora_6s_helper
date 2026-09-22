# 极光 6S 助手

插件 ID：`script.aurora_6s_helper`。适用于腾讯极光 6S（A4112）的 CoreELEC 21.x Amlogic-ng 和 CoreELEC 22 Amlogic-no。NG 驱动要求 4.9.269 / aarch64 内核及匹配的模块配置；NO 测试系统为 22.0-Piers_beta2。源码以工作区本目录为准，再打包和同步到盒子。

## 安装与使用

在 Kodi 的“插件 → 从 ZIP 文件安装”选择 `script.aurora_6s_helper-1.2.5.zip`，启用插件，然后在“程序插件”打开“极光 6S 助手”。主菜单有两个入口：

1. **修复硬件（自动识别 NG / NO）**：核对目标文件，确认机型后备份并安装。完成后可立即重启或稍后重启。
2. **设置灯条（常规 / 播放）**：设置两套颜色、亮度、常亮、呼吸或关闭、呼吸速度。点击“确定”保存后按当前播放状态应用；取消不会保存修改。

灯光默认启用：常规为蓝色、50%亮度、常亮；播放为蓝色、15%亮度、常亮。效果选“关闭”只关闭当前场景的RGB输出，保留颜色和亮度；切回常亮/呼吸或切换到其他场景会恢复对应效果。它不同于停用整个灯控服务，不会释放芯片并恢复原厂灯光。亮度为0也表示关灯。颜色控件使用 Kodi 原生色块色盘：方向键选择，OK 确认，设置页显示色块预览。精确输入在插件主菜单的“设置灯条 → 精确输入颜色值”中，支持六位 RRGGBB，例如 `0000FF` 蓝、`00FFFF` 青。旧版本六位颜色会自动迁移为原生控件的八位 AARRGGBB，RGB保持不变，灯条亮度仍由独立亮度选项控制。

音频、视频播放均使用播放设置，暂停仍视为播放；停止或播放结束恢复常规。保存设置时读取实际播放状态，修改非当前场景不会强行预览该场景。Kodi 启动时服务也会检测是否已有播放并应用对应设置。

设置由 Kodi 持久保存到当前用户配置目录的 `addon_data/script.aurora_6s_helper/settings.xml`，默认用户路径为 `/storage/.kodi/userdata/addon_data/script.aurora_6s_helper/settings.xml`。本插件没有额外的自定义 JSON 偏好文件。选择插件设置中的“查看当前灯光状态”可查看实际场景、后端和错误。

## 硬件修复

插件读取系统版本、分支和设备树标识。已使用本项目 6S DTB 的盒子可以自动识别；使用通用、Ugoos X4 或其他 SC2 设备树时，修复入口显示当前设备树型号及标识，必须点击“确认是6S并安装”才能继续。这个确认用于实物是6S但借用其他DTB的情况，不表示支持真正的X4等其他盒子。未确认时，命令行安装和灯控自动接管仍拒绝。手动确认只覆盖机型识别，不能绕过系统分支、版本、SC2平台和内核检查。

| 内容 | NG 21.x | NO 22 |
| --- | --- | --- |
| /flash/dtb.img | NG 专用 DTB，包含 PCIe 电源域修复 | NO 专用 DTB |
| rtl8852bs_config | 安装 | 安装 |
| Zidoo V12 识别规则 | 不安装、不修改已有规则 | 安装 |
| rtkm / 8852be / leds-tca6507 模块 | 安装到 /storage/.config/aurora6s-ng/ | 使用系统驱动 |
| systemd 驱动服务 | 安装并启用 Wi-Fi、LED 服务及 CE 原有 Realtek 蓝牙服务 | 不添加 |

两份 DTB 均使用原生 TCA6507 RGB 节点，默认纯蓝。NG 的 PCIe 节点关联电源域，防止启动阶段被当成空闲电源域关闭。蓝牙配置清除 0x01be 的 bit1，替代旧心跳保活；安装时停用旧 `bt-uart-keepalive.service`。NO 的 V12 规则保留键盘分类、去除错误 tablet 标签，不改鼠标接口。

**只需安装一次，NG 驱动由 systemd 在每次开机时自动加载。** 安装脚本创建 `/storage/.config/system.d/aurora6s-ng-wifi.service`、`aurora6s-ng-led.service` 和开机启动链接，另启用系统自带的 `rtkbt-firmware-aml.service`。服务调用 `/storage/.config/aurora6s-ng/load-drivers.sh`，不引用插件目录，不依赖 Kodi 运行。Wi-Fi 按 rtkm → 8852be 顺序加载，LED 服务加载 TCA6507 及需要的 I2C 接口。安装当次不在旧的运行中 DTB 上试加载，重启后统一生效。

从1.2.3起，不再按 `uname -v` 的编译日期或构建次数限制 NG 内核。安装和开机加载前检查 `4.9.269 / aarch64`，架构按 `CONFIG_ARM64` 核对（NG 的32位 Python 可报告 armv7l），以及 `CONFIG_SMP`、`CONFIG_PREEMPT`、`CONFIG_MODULE_UNLOAD`、`CONFIG_MODVERSIONS` 均为 y；同时保留分支和6S机型检查。实际 insmod 不使用 force 参数，由内核继续校验 vermagic 和导入符号 CRC。前置检查通过不代表全部接口兼容，发生符号版本不符仍需为该内核重新编译模块。Wi-Fi 加载还要求正确的 PCIe 电源域属性和 PCIe 设备存在。

保留 `/flash/config.ini`、配对记录和灯光偏好，不修改原厂 Android 或 eMMC。若还留着早期测试用的 PCIe/Wi-Fi 启动屏蔽参数，安装会提示先恢复正常配置。每次启动 Kodi 不会重复安装。卸载插件也不会卸载已安装的系统驱动服务。

每次安装先备份到 `/storage/ce-fix-backup/aurora-addon-*`，保存原文件、原来的启动链接、旧保活状态及哈希；原本不存在的文件在恢复时删除。写入失败会尝试自动回退，并恢复原来的 /flash 只读状态。手工恢复：在该备份目录执行 `sh restore.sh`，然后重启；无需保留插件。

无需启动 Kodi，也可以通过 SSH 执行同一个安装入口：

```sh
cd /storage/.kodi/addons/script.aurora_6s_helper
sh resources/scripts/install-repair.sh --check
sh resources/scripts/install-repair.sh --install
reboot
```

使用通用、X4等其他设备树且已确认实物为6S时，安装命令需追加 `--confirm-6s`。插件界面在确认对话框之后调用上述独立脚本。

## 灯控实现与边界

- 使用本机 I2C3/0x45 TCA6507，P0/P1/P2对应红/绿/蓝。仅在运行中的设备树 ID 为本项目原生RGB版本时接管。
- 接管前保存通道状态并解绑内核LED驱动，使用普通 I2C_SLAVE，不强占仍被驱动使用的地址。一个文件锁保证只有一个服务拥有芯片。
- 保持 sys_led 对应芯片使能常开。使用本插件期间，请将 CE 原有“系统指示灯”菜单保持 on；其 heartbeat/off 会复位或关闭芯片。
- 常亮使用芯片三个亮度源。默认开启“呼吸优先平滑”，由芯片执行硬件呼吸；若 RGB 有三种不同的非零亮度，将最接近的两种合并为平均档位，颜色会有所变化，保存的原始 RGB 不变。关闭此开关时，最多两种亮度仍用硬件呼吸，三种亮度改用约16ms更新的软件呼吸。常亮不做合并。
- 芯片只有16档亮度，低亮度颜色会量化甚至熄灭；RGB输入并不代表显示器级色准，实际颜色受灯珠影响。软件呼吸也是16档，低亮度时可能更明显看到阶梯。16ms更新可减少快速呼吸时跳过档位，但不会增加芯片的亮度档位；进入软件呼吸从熄灭开始，避免先闪到峰值。
- 呼吸速度快/中/慢对应每段渐亮或渐暗约768/1536/3072ms，峰值保持128ms，全暗停留分别为192/384/768ms，随速度同比例变化（1.2.2）。硬件内部有两次相同的呼吸阶段；不是双闪。
- 服务监听 Kodi 设置和播放通知，并每250ms检查状态以防漏事件。硬件效果不持续写寄存器；软件呼吸仅在量化输出发生变化时写入。
- 停用灯控或正常禁用/卸载插件时恢复原驱动及接管前灯光。Kodi 正常退出也会恢复；下次启动重新应用保存配置。异常终止留下的接管状态保存在 `/run`，下次服务接管沿用；断电后由系统初始化。
- Kodi 灯控服务仍需监听播放事件，仅负责颜色和效果。NG 驱动初始化由前述独立 systemd 服务完成，Kodi 服务不加载 NG 驱动。

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

NG 模块来源：`CoreELEC/RTL8852BE-aml` 提交 `5da5adca92ac38a6c3a782147d45e48da577dce3`（rtkm、8852be），`CoreELEC/linux-amlogic` 提交 `50c8e8154c5aba3c7cd8438d04dd5fdd51733e41`（leds-tca6507）。使用 NG 工作区已实机验证的模块，不包含诊断内核或临时 PCIe 模块。
