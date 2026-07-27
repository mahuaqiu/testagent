# 鸿蒙投屏 agent 资产说明

本目录存放鸿蒙 uitest daemon 投屏所需的 agent 组件（`uitest_agent_v{版本}.so`），
由 `worker/platforms/harmony_capture.py` 在启动帧流时推送到设备。

## 来源与版本

agent.so 是**华为官方 Hypium（DevEco Testing）投屏插件组件**，不随
OpenHarmony SDK / command-line-tools 分发，官方也没有独立的开源发布渠道，
只能从以下途径获取：

1. **DevEco Testing / Hypium 安装包提取（首选，版本最新）**：
   安装 DevEco Testing 后，在其安装目录或投屏插件目录中搜索 `agent.so`
   （通常位于投屏/屏幕录制插件资源内）；提取后按下方命名规范放入本目录。
2. **开源库跟踪（兜底）**：hmdriver2、hmnextauto 等社区库的 assets 目录会
   随版本携带 agent.so。注意这些库仅作**协议参考，不集成其代码**；
   其携带的 agent 版本可能滞后（如 awesome-hdc/hmdriver2 携带的 v1.0.7 已过旧）。

当前内置版本：

| 文件 | 大小 | 来源 | 说明 |
| --- | --- | --- | --- |
| `uitest_agent_v1.2.2.so` | 600245 B | hmnextauto | 首选版本 |
| `uitest_agent_v1.1.0.so` | 149686 B | hmdriver2 | 回退版本 |

## 命名与回退机制

- 命名规范：`uitest_agent_v{语义化版本}.so`，版本号必须体现在文件名中。
- `harmony_capture.py` 的 `AGENT_CANDIDATES` 按**新版优先**的顺序依次尝试；
  某版本启动帧流失败（推送失败/daemon 拒绝/startCaptureScreen 被拒）会自动
  回退下一候选；全部失败抛 `HarmonyCaptureError`，由 `HarmonyFrameSource`
  降级为 snapshot_display 轮询（约 1-2fps），投屏不中断。
- 新增版本时：文件放入本目录 → 将文件名插入 `AGENT_CANDIDATES` 首位即可，
  无需其它改动。

## 升级验证清单

替换/新增 agent 版本后，需在真机上验证：

1. **MD5 部署**：`harmony_capture` 会按 MD5 比对决定是否重新推送到
   `/data/local/tmp/agent.so` 并 `chmod +x`，确认设备侧文件已更新。
2. **协议兼容**：socket 发送 `Captures/startCaptureScreen` 后回复须含
   `"true"`（v1.0.7 → v1.2.2 协议未变，但新版本不保证永远兼容）。
3. **帧流质量**：JPEG 流按 FFD8/FFD9 魔数切帧正常、帧率约 10fps。
4. **回退路径**：故意用损坏文件验证候选回退与轮询降级仍然生效。

## 运行注意事项

- uitest daemon 是**单例进程**：启动前 `harmony_capture` 会 kill 残留的
  `uitest start-daemon singleness` 进程再重启，否则连接 8012 端口会失败。
- 帧流走 `hdc fport tcp:{本地空闲端口} tcp:8012`，停止时必须 `fport rm`
  清理规则（`stop()`/`_cleanup()` 已处理，异常路径需留意）。
- 鸿蒙 PC 形态的 daemon/agent 可用性尚未在真机验证，PC 上帧流启动失败
  属预期内行为（自动降级轮询）。
