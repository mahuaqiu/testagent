# 项目说明（供 AI Agent 阅读）

这是一个 **多端自动化测试执行基建**，作为 Worker 提供 HTTP API 供外部平台调度执行自动化测试任务。

## 核心特性

- **多平台支持**：Web、Android、iOS、Windows、Mac
- **OCR/图像识别定位**：所有平台统一使用 OCR 和图像识别定位，不依赖传统元素选择器
- **设备自动发现**：启动时自动发现本机设备和环境
- **平台上报**：支持向配置平台上报 Worker 状态和设备信息
- **设备热插拔监控**：定时检测移动设备连接变化（60秒）
- **并发执行**：支持多设备并行执行任务

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
│       └── result.py          # 结果模型
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
| `/status` | GET | Worker 完整状态信息（含设备列表、支持平台等） |
| `/task/execute` | POST | 同步执行任务 |
| `/task/{task_id}` | GET | 查询任务结果 |
| `/session` | POST | 创建会话 |
| `/session/{session_id}` | DELETE | 关闭会话 |
| `/devices/refresh` | POST | 刷新设备列表 |

**说明**：截图功能通过 `task/execute` 接口的 `screenshot` 动作实现，不再提供单独的截图接口。

## 动作类型

所有平台统一使用以下动作类型，基于 OCR/图像识别定位：

| 类型 | 说明 |
|------|------|
| `ocr_click` | 点击识别到的文字 |
| `ocr_input` | 在文字附近输入 |
| `ocr_wait` | 等待文字出现 |
| `ocr_assert` | 断言文字存在 |
| `image_click` | 点击匹配的图像 |
| `image_wait` | 等待图像出现 |
| `image_assert` | 断言图像存在 |
| `click` | 坐标点击 |
| `swipe` | 滑动 |
| `input` | 坐标输入 |
| `press` | 按键 |
| `screenshot` | 截图 |
| `navigate` | 跳转 URL (Web) |
| `launch_app` | 启动应用 |

## 任务并发策略

| 终端类型 | 并发规则 |
|----------|----------|
| Windows/Mac/Web | 同一时刻只能执行一个任务 |
| Android 设备 | 每台设备独立，同台设备排队 |
| iOS 设备 | 每台设备独立，同台设备排队 |

## 运行方式

### 安装依赖

```bash
pip install -e "."
playwright install chromium
```

### 启动 Worker

```bash
# 基本启动
python -m worker.main

# 指定端口
python -m worker.main --port 8080

# 指定 OCR 服务
python -m worker.main --ocr-service http://localhost:8081

# 指定配置平台
python -m worker.main --platform-api http://platform.example.com/api/worker
```

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
    {"action_type": "ocr_input", "value": "用户名", "offset": {"x": 100, "y": 0}},
    {"action_type": "screenshot", "value": "result"}
  ]
}
```

## 重要说明

1. **OCR 服务独立部署**：本工程通过 HTTP 调用外部 OCR 服务，不包含 OCR 实现代码
2. **测试用例分离**：测试用例写在其他工程，本工程只作为执行基建
3. **设备监控**：移动设备热插拔检测间隔为 60 秒
4. **会话管理**：支持创建会话并复用，用例结束时调用删除接口