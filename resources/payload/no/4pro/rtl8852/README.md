# 4 Pro / RTL8852 / NO

A4111 / S905X4 rev D 使用与 6S NO 相同的修复方案，包含 DTB、Realtek config18 和 V12 输入规则。
DTB 以 `no/6s/dtb.img` 为基础，仅更改 `model` 和 `coreelec-dt-id`：
`sc2_s905x4_tencent_aurora_4pro_rtl8852`。

该对应关系由用户确认；属性对照、安装/恢复测试已覆盖，尚未进行 4 Pro RTL8852 实机测试。
