# 鸿蒙性能真机命令样例归档说明

`harmony_pc/` 和 `harmony_mobile/` 是 PC 与移动端 P0/可选命令原始输出的归档目录。
当前只创建目录，尚无真机输出，因此不能据此宣称 P0 解析或 Worker-HDC E2E 已完成。

每份归档至少记录：设备类型、产品类型、系统版本、HDC 版本、UDID 脱敏标识、采集时间，
以及对应命令名。原始输出进入目录后，先更新 `harmony-perf-command-contract.md` 和
`perfharmony/docs/command-contract.md`，再评审解析器字段与单位。
