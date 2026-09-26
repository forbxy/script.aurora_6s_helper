# 4 Pro / RTL8852 / NO

A4111 / S905X4 rev D 使用与 6S NO 相同的修复方案，包含 DTB、Realtek config18 和 V12 输入规则。
DTB 以 `no/6s/dtb.img` 为基础，仅更改 `model` 和 `coreelec-dt-id`：
`sc2_s905x4_tencent_aurora_4pro_rtl8852_hs400`。

该对应关系由用户确认；属性对照、安装/恢复测试已覆盖，尚未进行 4 Pro RTL8852 实机测试。

自 1.6.1 起，默认启用 eMMC HS400，`tx_delay=16`；dt-id 使用 `_hs400` 后缀，区别于官方标识。4 Pro/AP6275P 已通过本机基础读写测试，其他硬件版本仍需实机确认。
