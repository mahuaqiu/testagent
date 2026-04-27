# iOS 连接方案切换：tidevice3 → go-ios

## 背景

tidevice3 在 iOS 17+ 上存在较多 BUG，导致 iOS 自动化测试无法稳定运行。go-ios 是一个更成熟的开源方案，支持 iOS 17+ 的新 tunnel 协议。

## 目标

将 iOS 平台的设备连接、WDA 启动、端口转发等功能从 tidevice3 完全切换到 go-ios。

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 管理员权限 | 始终以管理员运行 | 使用内核 tunnel，性能更好 |
| 端口分配 | 按设备序号递增 | 支持多设备并发操作 |
| go-ios 部署 | 放入 tools 目录 | 打包时分发，路径可控 |
| 切换策略 | 完全替换 | 简化维护，避免兼容性复杂性 |

## 架构设计

### 核心流程

```
Worker 启动
    ↓
检查 go-ios agent 是否运行（GET localhost:28100/health）
    ↓
├─ 运行正常 → 复用现有 agent
├─ 运行异常 → 杀掉进程，重新启动 agent
└─ 未运行 → 启动 agent
    ↓
设备发现（ios list --details）
    ↓
iOS 17+ 设备 → 等待 tunnel 建立（轮询 /tunnel/{udid}）
    ↓
任务执行请求
    ↓
ensure_device_service()
    ├─ 检查端口是否有可用 WDA → 复用
    ├─ 端口被占用但 WDA 不可用 → 杀掉重启
    └─ 端口空闲 → 启动新 WDA
    ↓
执行动作（ios launch/kill 或 WDA HTTP API）
```

### 端口分配策略

| 设备 | WDA 端口 | MJPEG 端口 | 计算方式 |
|------|----------|------------|----------|
| 设备 1 | 8100 | 9100 | wda_base_port + 0 |
| 设备 2 | 8101 | 9101 | wda_base_port + 1 |
| 设备 N | 8100+N-1 | 9100+N-1 | wda_base_port + (index-1) |

设备索引根据 UDID 在 `ios list` 输出列表中的位置动态计算。

### 进程管理原则

- **go-ios agent、runwda、forward 进程**：使用 `DETACHED_PROCESS` 独立运行
- **Worker stop()**：只清理内存引用，不关闭进程
- **Worker start()**：检测现有进程是否可用，异常则重启

## 配置变更

### worker.yaml

```yaml
ios:
  enabled: null
  go_ios_path: tools/go-ios/ios.exe  # go-ios 可执行文件路径（相对于 exe 目录）
  agent_port: 28100                   # go-ios agent HTTP API 端口
  wda_base_port: 8100                 # WDA 基础端口
  mjpeg_base_port: 9100               # MJPEG 基础端口
  wda_bundle_id: com.facebook.WebDriverAgentRunner.majy.xctrunner
  session_timeout: 300
  screenshot_dir: data/screenshots
```

**移除配置项**：
- `tunneld_port` - go-ios agent 使用固定端口 28100
- `tunneld_enabled` - tunnel 功能由 agent 自动管理

## 代码实现

### 新增 GoIOSClient 类

封装 go-ios CLI 命令调用，提供以下方法：

| 方法 | go-ios 命令 | 说明 |
|------|-------------|------|
| `list_devices()` | `ios list --details` | 获取设备列表（含版本、名称） |
| `get_tunnel_info(udid)` | `GET localhost:28100/tunnel/{udid}` | 获取 iOS 17+ tunnel 信息 |
| `launch_app()` | `ios launch {bundle_id}` | 启动应用 |
| `kill_app()` | `ios kill {bundle_id}` | 关闭应用 |
| `get_processes()` | `ios ps --apps` | 获取运行的应用进程 |
| `start_wda()` | `ios runwda` | 启动 WDA（后台进程） |
| `forward_port()` | `ios forward {local} {device}` | 端口转发（后台进程） |
| `start_agent()` | `ios tunnel start` | 启动 agent（后台进程） |
| `check_agent_health()` | `GET localhost:28100/health` | 检查 agent 健康状态 |

**命令调用特性**：
- 所有命令输出 JSON，便于解析
- iOS 17+ 设备传递 `--address` 和 `--rsd-port` 参数
- 后台进程使用 `DETACHED_PROCESS` + `STARTUPINFO` 隐藏窗口

### iOSPlatformManager 重构

**方法变更对照**：

| 方法 | tidevice3 实现 | go-ios 实现 |
|------|---------------|-------------|
| `start()` | 启动 tunneld + 检测 tidevice3 | 启动/复用 go-ios agent |
| `stop()` | 停止 tunneld + 清理引用 | 只清理内存引用（进程保持） |
| `get_online_devices()` | tidevice3 API | `ios list --details` |
| `ensure_device_service()` | t3 runwda + relay | ios runwda + forward |
| `_start_wda()` | t3 runwda --dst-port | ios runwda + forward |
| `_get_device_version()` | tidevice3 API | `ios list --details` |
| `_action_start_app()` | t3 app launch | `ios launch` |
| `_action_stop_app()` | t3 app ps + kill | `ios ps` + `ios kill` |

**新增方法**：
- `_get_go_ios_path()` - 获取 go-ios exe 完整路径
- `_ensure_agent_running()` - 确保 agent 正在运行，异常则重启
- `_get_tunnel_info(udid)` - 获取设备 tunnel 信息（address + rsdPort）
- `_get_device_index(udid)` - 根据设备列表位置计算索引
- `_allocate_ports(udid)` - 根据索引分配 WDA/MJPEG 端口

**移除方法**：
- `_start_tunneld()` - 由 go-ios agent 管理
- `_check_ios17_devices()` - agent 自动处理
- `_check_tunneld_running()` - 使用 agent health check
- `_start_mjpeg_relay()` - 使用 `ios forward` 替代

## 错误处理

| 场景 | 处理策略 |
|------|----------|
| go-ios agent 未启动 | Worker 启动时检测并启动，失败禁用 iOS 平台 |
| agent health check 失败 | 杀掉 agent 进程，重新启动 |
| iOS 17+ tunnel 未建立 | 轮询 `/tunnel/{udid}`，超时 30s 返回 faulty |
| WDA 启动失败 | 重试 3 次，失败标记 faulty |
| 端口被占用 | go-ios 进程占用则复用，否则杀掉占用进程 |
| 设备断开 | agent 自动清理，Worker 清理内存引用 |
| 命令超时 | 默认 30s 超时，超时返回错误 |

## 依赖变更

**移除依赖**：
- tidevice3（pip 包）

**新增依赖**：
- go-ios.exe（tools/go-ios/ios.exe）
- wintun.dll（tools/go-ios/wintun.dll，Windows 内核 tunnel 需要）

## 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `worker/platforms/go_ios_client.py` | 新增 | GoIOSClient 类封装 go-ios CLI 命令 |
| `worker/platforms/ios.py` | 重写 | iOSPlatformManager 使用 go-ios |
| `worker/config.py` | 修改 | 新增 go_ios_path、agent_port、mjpeg_base_port 配置项 |
| `config/worker.yaml` | 修改 | iOS 配置结构变更 |
| `worker/discovery/ios.py` | 修改 | iOS 设备发现模块切换到 go-ios |
| `pyproject.toml` | 修改 | 移除 tidevice3 依赖 |

## 测试计划

1. 单设备 WDA 启动和动作执行
2. 多设备并发任务执行（端口分配验证）
3. iOS 17+ 和 iOS 16 设备混合测试
4. Worker 重启后进程复用验证
5. agent 异常自动恢复验证
6. 设备断开/重连场景验证