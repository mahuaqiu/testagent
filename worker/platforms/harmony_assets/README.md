# 鸿蒙投屏 agent 说明

## 现状（2026-10 起）

本目录**不再携带 agent.so**。设备上的 `/data/local/tmp/agent.so` 由**官方
HOScrcpy SDK**（`tools/harmony/hosScrcpy-*.jar`，Java 官方连接
`startCaptureScreen` 引导）自动安装/更新：

- SDK 每次引导读取设备 agent 内嵌版本号，与 jar 内 `uitest_agent_1.2.3.so`
  不一致时自动执行 `force stop uitest → remove agent → 推送 → 重启`；
  一致时直接跳过（`Device agent is up to date!`）。
- worker 的 JPEG 帧流（`harmony_capture.py`）**不推送**，直接使用设备上
  已有版本，避免与 SDK 互相覆盖文件、互杀 uitest daemon（历史问题）。

## 帧流启动流程（harmony_capture.py）

1. 探测设备 agent.so，缺失时打告警日志；
2. 按需重启 uitest start-daemon singleness（8012 已监听则跳过）；
3. hdc fport 本地空闲端口 → 设备 8012；
4. socket 发送 `Captures/startCaptureScreen`，持续收取 JPEG（约 10fps）。

握手失败且疑似 daemon 僵死时强制重启重试一次，仍失败抛
`HarmonyCaptureError`，由 `HarmonyFrameSource` 降级为 snapshot_display
轮询（约 1-2fps），投屏不中断。

## 运行注意事项

- **全新设备第一次只跑 JPEG 时**：设备上还没有 agent.so，帧流会失败并
  降级轮询；先走一次 H264 远程（或 HOScrcpy 官方工具）即可完成安装。
- uitest daemon 是**单例进程**：`kill -9` 会连带杀掉官方 SDK 的
  libscreen_casting 投屏守护——两者共用同一个 daemon，操作时需注意。
- 升级 HOScrcpy jar 后，SDK 会自动替换设备上的 agent 版本，无需手动干预。
- 鸿蒙 PC 形态的 daemon/agent 可用性尚未在真机验证，PC 上帧流启动失败
  属预期内行为（自动降级轮询）。
