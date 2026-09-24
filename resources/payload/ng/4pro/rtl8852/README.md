# 4 Pro / RTL8852 / NG

A4111 / S905X4 rev D 使用与 6S NG 相同的修复方案。
DTB 以 `ng/6s/dtb.img` 为基础，仅更改 `model` 和 `coreelec-dt-id`：
`sc2_s905x4_tencent_aurora_4pro_rtl8852_ng`。

驱动模块、Realtek config18 和 systemd 单元与 6S 相同；加载脚本校验此 4 Pro dt-id。
为兼容原安装和回退，安装位置仍为 `/storage/.config/aurora6s-ng/`，服务名仍为 `aurora6s-ng-*`。
驱动仍由 systemd 在启动时加载，Kodi 只负责灯光设置。

该对应关系由用户确认；属性对照、安装/恢复测试已覆盖，尚未进行 4 Pro RTL8852 实机测试。
