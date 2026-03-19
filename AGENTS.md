# 项目说明（供 AI Agent 阅读）

这是一个 **多端自动化测试执行基建**，作为 Worker 提供 HTTP API 供外部平台调度执行自动化测试任务。

## 核心特性

- **多平台支持**：Web、Android、iOS、Windows、Mac
- **OCR/图像识别定位**：所有平台统一使用 OCR 和图像识别定位，不依赖传统元素选择器
- **设备自动发现**：启动时自动发现本机设备和环境
- **平台上报**：支持向配置平台上报 Worker 状态和设备信息
- **设备热插拔监控**：定时检测移动设备连接变化（60秒）
- **并发执行**：支持多设备并行执行任务
- **失败自动截图**：任务执行失败时自动返回设备截图

## 平台支持策略

| 宿主机 | 支持平台 | 移动设备 |
|--------|----------|----------|
| Windows | Web + Windows + Android + iOS | 连接在本机 |
| macOS | Mac | 不支持 |

## 技术栈

- 语言: Python 3.10+
- HTTP 服务: FastAPI + Uvicorn
- Web 自动化: Playwright
- 移动端自动化: Appium (UiAutomator2 / XCUITest)
- 桌面自动化: pyautogui
- OCR 服务: 外部 HTTP 服务

## 目录结构

```
autotest/
├── worker/                     # Worker 核心
│   ├── main.py                # 统一入口
│   ├── server.py              # HTTP Server (FastAPI)
│   ├── worker.py              # 主服务（设备发现、任务调度）
│   ├── config.py              # 配置管理
│   │
│   ├── discovery/             # 设备发现模块
│   │   ├── host.py            # 宿主机发现
│   │   ├── android.py         # Android 设备发现 (ADB)
│   │   └── ios.py             # iOS 设备发现 (libimobiledevice)
│   │
│   ├── reporter/              # 平台上报模块
│   │   ├── client.py          # HTTP 上报客户端
│   │   └── models.py          # 上报数据模型
│   │
│   ├── platforms/             # 平台执行引擎
│   │   ├── base.py            # 平台基类
│   │   ├── web.py             # Web 平台 (Playwright + OCR)
│   │   ├── android.py         # Android 平台 (Appium + OCR)
│   │   ├── ios.py             # iOS 平台 (Appium + OCR)
│   │   ├── windows.py         # Windows 桌面 (pyautogui + OCR)
│   │   └── mac.py             # Mac 桌面 (pyautogui + OCR)
│   │
│   └── task/                  # 任务模型
│       ├── task.py            # 任务定义
│       ├── action.py          # 动作定义（OCR/图像识别驱动）
│       ├── result.py          # 结果模型
│       └── store.py           # 内存任务存储（异步任务管理）
│
├── common/                     # 公共库
│   ├── config.py              # 配置管理
│   ├── utils.py               # 工具函数
│   └── ocr_client.py          # OCR 服务客户端
│
├── config/
│   └── worker.yaml            # Worker 配置
│
├── scripts/                    # 打包脚本
│   ├── build_windows.ps1      # Windows 打包
│   ├── build_mac.sh           # Mac 打包
│   └── pyinstaller.spec       # PyInstaller 配置
│
└── pyproject.toml             # 依赖管理
```

## HTTP API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/worker_devices` | GET | Worker 状态和设备信息 |
| `/task/execute` | POST | 同步执行任务（不返回 task_id） |
| `/task/execute_async` | POST | 异步执行任务（返回 task_id） |
| `/task/{task_id}` | GET | 查询任务结果（一次性，查询后销毁） |
| `/task/{task_id}` | DELETE | 取消任务 |
| `/devices/refresh` | POST | 刷新设备列表 |

**说明**：
- 截图功能通过 `screenshot` 动作实现，不再提供单独的截图接口
- `/task/execute` 同步执行，阻塞等待结果，不生成 task_id
- `/task/execute_async` 异步执行，立即返回 task_id，任务在后台执行
- `/task/{task_id}` GET 查询结果后会从内存中销毁，再次查询返回 404

## 动作类型

所有平台统一使用以下动作类型，基于 OCR/图像识别定位：

### 通用动作（所有平台支持）

| 类型 | 说明 | 传参示例 |
|------|------|----------|
| `ocr_click` | 点击识别到的文字 | `{"action_type": "ocr_click", "value": "登录", "offset": {"x": 0, "y": 0}, "timeout": 5000}` |
| `ocr_input` | 在文字附近输入 | `{"action_type": "ocr_input", "value": "用户名", "text": "admin", "offset": {"x": 100, "y": 0}}` |
| `ocr_wait` | 等待文字出现 | `{"action_type": "ocr_wait", "value": "确认", "timeout": 10000}` |
| `ocr_assert` | 断言文字存在 | `{"action_type": "ocr_assert", "value": "成功", "timeout": 5000}` |
| `ocr_get_text` | 获取屏幕文字 | `{"action_type": "ocr_get_text", "value": ""}` |
| `image_click` | 点击匹配的图像 | `{"action_type": "image_click", "value": "button.png", "threshold": 0.8}` |
| `image_wait` | 等待图像出现 | `{"action_type": "image_wait", "value": "icon.png", "timeout": 10000}` |
| `image_assert` | 断言图像存在 | `{"action_type": "image_assert", "value": "logo.png", "threshold": 0.8}` |
| `click` | 坐标点击 | `{"action_type": "click", "x": 500, "y": 300}` |
| `swipe` | 滑动 | `{"action_type": "swipe", "from": {"x": 500, "y": 1000}, "to": {"x": 500, "y": 500}, "duration": 500}` |
| `input` | 坐标输入 | `{"action_type": "input", "x": 500, "y": 300, "text": "hello"}` |
| `press` | 按键 | `{"action_type": "press", "key": "Enter"}` |
| `screenshot` | 截图 | `{"action_type": "screenshot", "value": "result"}` |
| `wait` | 固定等待 | `{"action_type": "wait", "value": 1000}` |
| `start_app` | 启动应用/浏览器 | `{"action_type": "start_app", "value": "chromium"}` (Web) 或 `{"action_type": "start_app", "value": "com.example.app"}` (Android/iOS) |
| `stop_app` | 关闭应用/浏览器 | `{"action_type": "stop_app"}` 或 `{"action_type": "stop_app", "value": "com.example.app"}` |

**参数说明：**
- `value`: 动作核心值（文字、图像路径、等待毫秒数、坐标等），**必填**
- `offset`: 相对于识别结果的偏移量 `{"x": 横向偏移, "y": 纵向偏移}`，**可选**
- `timeout`: 超时时间（毫秒），默认 5000，**可选**
- `threshold`: 图像匹配阈值，默认 0.8，**可选**
- `text`: 输入的文本内容，**必填**（用于 ocr_input、input）
- `x`, `y`: 坐标位置，**必填**（用于 click、input）
- `from`, `to`: 滑动起始和结束坐标，**必填**（用于 swipe）
- `duration`: 滑动持续时间（毫秒），**可选**
- `key`: 按键名称（如 Enter, Escape、ArrowDown 等），**必填**（用于 press）

### Web 特有动作

| 类型 | 说明 | 传参示例 |
|------|------|----------|
| `navigate` | 跳转 URL | `{"action_type": "navigate", "value": "https://example.com"}` |

## 任务并发策略

| 终端类型 | 并发规则 |
|----------|----------|
| Windows/Mac/Web | 同一时刻只能执行一个任务 |
| Android 设备 | 每台设备独立，同台设备排队 |
| iOS 设备 | 每台设备独立，同台设备排队 |

## 任务执行流程

1. **前置验证**
   - 平台支持验证：检查请求的平台是否支持
   - device_id 验证：移动端必须提供 device_id，且设备必须存在
   - action_type 验证：检查所有动作类型是否支持

2. **状态检查**
   - 检查目标设备是否处于任务中
   - 如果忙碌则返回错误 "Device is busy"

3. **执行任务**
   - 创建执行上下文（Web 创建 Page，移动端创建 Driver）
   - 依次执行动作列表
   - 失败时自动获取设备截图

4. **清理资源**
   - 关闭执行上下文
   - 恢复设备状态为空闲

## 运行方式

### 安装依赖

```bash
pip install -e "."
playwright install chromium
```

### 启动 Worker

```bash
# 直接启动，配置从 config/worker.yaml 读取
python -m worker.main
```

**说明**：所有配置项均在 `config/worker.yaml` 中设置，启动时无需传递任何参数。

### 打包

```bash
# Mac
./scripts/build_mac.sh

# Windows (PowerShell)
.\scripts\build_windows.ps1
```

## 配置文件示例

```yaml
# config/worker.yaml
worker:
  id: null                          # 自动生成
  port: 8080
  device_check_interval: 60

external_services:
  platform_api: ""                  # 配置平台 API
  ocr_service: "http://127.0.0.1:8081"

platforms:
  web:
    headless: true
    browser_type: chromium

  android:
    appium_server: "http://127.0.0.1:4723"

  ios:
    appium_server: "http://127.0.0.1:4724"
```

## 任务请求示例

```json
{
  "platform": "web",
  "actions": [
    {"action_type": "navigate", "value": "https://example.com"},
    {"action_type": "ocr_click", "value": "登录"},
    {"action_type": "screenshot", "value": "result"}
  ]
}
```

## 任务结果示例

### 成功结果

```json
{
  "status": "success",
  "platform": "web",
  "duration_ms": 1500,
  "actions": [
    {"index": 0, "action_type": "navigate", "status": "success", "duration_ms": 500},
    {"index": 1, "action_type": "ocr_click", "status": "success", "duration_ms": 1000}
  ]
}
```

### 失败结果（含截图）

```json
{
  "status": "failed",
  "platform": "web",
  "duration_ms": 500,
  "actions": [
    {"index": 0, "action_type": "ocr_click", "status": "failed", "error": "Text not found: 登录"}
  ],
  "error": "Text not found: 登录",
  "error_screenshot": "base64_encoded_screenshot_data"
}
```

### 异步任务立即返回

```json
{
  "task_id": "task_20260317_120000_abc123",
  "status": "running"
}
```

### 异步任务查询

```json
{
  "task_id": "task_20260317_120000_abc123",
  "status": "success",
  "platform": "web",
  "duration_ms": 1500,
  "actions": [...]
}
```

## 重要说明

1. **OCR 服务独立部署**：本工程通过 HTTP 调用外部 OCR 服务，不包含 OCR 实现代码
2. **测试用例分离**：测试用例写在其他工程，本工程只作为执行基建
3. **设备监控**：移动设备热插拔检测间隔为 60 秒
4. **无状态设计**：Worker 不维护会话状态，每次任务独立执行