# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

多端自动化测试执行基建，作为 Worker 提供 HTTP API 供外部平台调度执行自动化测试任务。

**核心设计原则**：所有平台统一使用 OCR 和图像识别定位元素，不依赖传统元素选择器（如 XPath、CSS Selector）。

## 常用命令

```bash
# 安装依赖
pip install -e "."
playwright install chromium

# 启动 Worker
python -m worker.main

# 代码检查
ruff check .
black .

# 运行测试
pytest

# 打包（Windows）
powershell scripts/build_windows.ps1

# 打包（Mac）
./scripts/build_mac.sh
```

## 核心架构

### 执行流程

1. **Worker** (`worker/worker.py`) - 主服务，管理设备发现、平台管理器初始化、任务调度
2. **Server** (`worker/server.py`) - FastAPI HTTP 服务，接收任务请求
3. **DeviceMonitor** (`worker/device_monitor.py`) - 独立设备监控模块，管理设备服务状态
4. **PlatformManager** (`worker/platforms/base.py`) - 平台抽象基类，定义统一接口
5. **OCRClient** (`common/ocr_client.py`) - OCR 服务客户端，被所有平台共享

### 平台管理器模式

每个平台（Web/Android/iOS/Windows/Mac）实现 `PlatformManager` 基类：

```
PlatformManager (抽象基类)
├── start() / stop() - 生命周期管理
├── create_context() / close_context() - 执行上下文管理
├── execute_action() - 执行动作（核心方法）
├── get_screenshot() - 获取截图
├── ensure_device_service() - 确保设备服务可用（移动端）
├── mark_device_faulty() - 标记设备异常（移动端）
├── get_online_devices() - 获取在线设备列表（移动端）
└── BASE_SUPPORTED_ACTIONS - 通用动作集（ocr_click, image_click 等）
```

**新增平台**：继承 `PlatformManager`，实现抽象方法，在 `worker.py` 的 `_init_platform_managers()` 中注册。

### 并发控制

- **Windows/Mac/Web**：全局单任务锁
- **Android/iOS 设备**：按设备 ID 独立锁，同设备排队

### 配置管理

所有配置从 `config/worker.yaml` 加载，包括：
- OCR 服务地址（必须配置）
- 平台 API 地址
- 设备监控间隔（默认 300 秒）
- 平台特定参数（browser_type, wda_base_port, wda_ipa_path 等）

## 平台支持策略

| 宿主机 | 支持平台 | 移动设备 |
|--------|----------|----------|
| Windows | Web + Windows + Android + iOS | 连接在本机 |
| macOS | Mac | 不支持 |

## HTTP API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/worker_devices` | GET | Worker 状态和设备信息 |
| `/task/execute` | POST | 同步执行任务（不返回 task_id） |
| `/task/execute_async` | POST | 异步执行任务（返回 task_id） |
| `/task/{task_id}` | GET | 查询任务结果（一次性，查询后销毁） |
| `/task/{task_id}` | DELETE | 取消任务 |
| `/devices/refresh` | POST | 刷新设备列表 |

## 动作类型

所有动作基于 OCR/图像识别或坐标定位。核心动作：
- **OCR 动作**：`ocr_click`, `ocr_input`, `ocr_wait`, `ocr_assert`, `ocr_get_text`, `ocr_paste`, `ocr_move`, `ocr_exist`
- **图像动作**：`image_click`, `image_wait`, `image_assert`, `image_click_near_text`, `image_move`, `image_exist`
- **坐标动作**：`click`, `right_click`, `move`, `swipe`, `drag`, `input`, `press`
- **其他**：`screenshot`, `wait`, `start_app`, `stop_app`
- **Web 特有**：`navigate`, `new_page`, `switched_page`, `close_page`
- **命令执行**：`cmd_exec` - 执行宿主机命令，支持 `@tools/脚本名` 占位符
- **移动端特有**：`unlock_screen` - 解锁屏幕（iOS/Android 专用）

**OCR 统一匹配策略**：精确匹配 → 模糊匹配，`reg_` 前缀使用正则匹配。

**region 操作区域**：所有 OCR/Image 动作支持 `region` 参数 `[x1, y1, x2, y2]`，限制操作在屏幕指定矩形区域内执行。适用于多画面会场场景，只关注屏幕某一部分的变化。

### 动作参数

| 参数 | 说明 | 适用动作 |
|------|------|----------|
| `value` | 文字/URL/按键值/页面索引，`reg_` 前缀表示正则匹配 | 所有 OCR 动作、press、navigate、switched_page、cmd_exec |
| `value` | 命令字符串，`@tools/脚本名` 自动替换为完整脚本路径 | cmd_exec |
| `x`, `y` | 目标坐标（或拖拽起点） | click, right_click, move, swipe, drag, input |
| `image_base64` | 图像模板 base64 编码 | image_* 动作 |
| `index` | 选择第几个匹配结果（默认 0） | ocr_click, ocr_input, ocr_paste, ocr_move, ocr_exist, image_click, image_wait, image_assert, image_move, image_exist |
| `offset` | 点击偏移 `{"x": 10, "y": 5}` | 所有点击类动作、move 类动作 |
| `threshold` | 图像匹配阈值（默认 0.8） | image_* 动作 |
| `timeout` | 超时时间（默认 30000ms） | wait 类动作 |
| `end_x`, `end_y` | 拖拽终点坐标 | swipe, drag |
| `region` | 操作区域 `[x1, y1, x2, y2]`，限制 OCR/图像识别在指定矩形区域内执行 | 所有 ocr_* 和 image_* 动作 |
| `level` | 执行层级：`browser`（Playwright）或 `system`（pyautogui），仅 Web 平台支持 | 所有动作 |

### swipe / drag 动作说明

`swipe` 和 `drag` 功能相同，都是从起点拖拽到终点（按下 → 移动 → 松开）。`drag` 是更语义化的命名，适合桌面端拖拽元素场景。

```json
{
  "action_type": "drag",
  "x": 100,
  "y": 200,
  "end_x": 400,
  "end_y": 600
}
```

### level 执行层级（Web 平台专用）

用于处理浏览器原生对话框等场景，Playwright 无法截取/操作浏览器外部的原生 UI。

| 值 | 说明 | 适用场景 |
|----|------|----------|
| `browser` | 使用 Playwright 操作浏览器内部内容（默认） | 正常 Web 页面操作 |
| `system` | 使用系统级操作（mss 截屏 + pyautogui 点击） | 原生对话框、文件选择器、权限弹窗 |

**使用示例**：
```json
// 点击原生共享对话框中的"屏幕 1"
{"action_type": "ocr_click", "value": "屏幕 1", "level": "system"}

// 系统级截图（截取第一个显示器）
{"action_type": "screenshot", "value": "native", "level": "system"}
```

### monitor 显示器选择（配合 level: system）

指定系统级截图截取哪个显示器：

| 值 | 说明 |
|----|------|
| `1` | 第一个显示器（默认） |
| `2` | 第二个显示器 |

**使用示例**：
```json
// 截取第二个显示器上的原生对话框
{"action_type": "ocr_click", "value": "确认", "level": "system", "monitor": 2}
```

**依赖**：`level: system` 需要 `mss` 和 `pyautogui` 库。

### image_click_near_text 说明

点击文本附近最近的图片。用于场景如：点击"密码"文字附近的输入框图标。

```json
{
  "action_type": "image_click_near_text",
  "image_base64": "<base64_encoded_image>",
  "value": "密码",
  "end_x": 500,
  "threshold": 0.8
}
```
}

### unlock_screen 解锁屏幕（iOS/Android 专用）

检测设备锁屏状态并自动解锁。使用固定坐标点击密码键盘，不依赖 OCR。

**使用示例**：
```json
{
  "action_type": "unlock_screen",
  "value": "123456"
}
```

**执行流程**：
1. 检测锁屏状态（iOS: `/wda/locked`，Android: `device.info['screenOn']`）
2. 唤醒屏幕（如熄屏）
3. 触发密码界面（根据机型配置选择方式）
4. 输入密码（固定坐标点击，带间隔）
5. 验证解锁成功

**iOS 解锁方式**（按机型配置）：
- `home_key`：唤醒后按 HOME 键出现密码界面（iPhone 8/SE 等 Touch ID 机型）
- `swipe_up`：向上滑动出现密码界面（iPhone X/11/14 等 Face ID 机型）

**配置项**（`config/worker.yaml`）：
- `unlock.click_interval`: 点击间隔（毫秒），默认 150
- `unlock.ios_unlock_method`: iOS 解锁方式配置（按分辨率）
- `unlock.ios_keypad`: iOS 密码键盘坐标配置（物理分辨率）
- `unlock.android_keypad`: Android 密码键盘坐标配置（物理分辨率）

## 任务执行流程

1. 前置验证：平台支持、device_id（移动端必填）、action_type
2. 状态检查：目标设备是否忙碌
3. 创建执行上下文并执行动作列表
4. 失败时自动获取设备截图
5. 清理资源（不关闭会话，保持资源复用）

## 重要说明

- **OCR 服务独立部署**：本工程通过 HTTP 调用外部 OCR 服务
- **测试用例分离**：本工程只作为执行基建，不包含测试用例
- **设备监控**：移动设备检测间隔 300 秒（5 分钟），支持设备服务自动启动和恢复
- **直连模式**：Android 使用 uiautomator2 直连，iOS 使用 tidevice3 + WDA 直连，无需 Appium Server
- **无状态设计**：Worker 不维护会话状态，每次任务独立执行

## 脚本执行机制

Worker 支持通过 `cmd_exec` action 执行外部脚本（PowerShell/Shell），用于复杂任务如播放媒体、软件安装等。

### tools 目录

脚本存放在 `tools/` 目录，打包时带入 exe 目录：
- `play_ppt.ps1` - 播放 PowerPoint
- `download_install.ps1` - 下载解压安装

### 调用方式

使用 `@tools/` 占位符，自动替换为完整路径：

```json
{
  "action_type": "cmd_exec",
  "value": "powershell -ExecutionPolicy Bypass -File \"@tools/play_ppt.ps1\" -FilePath \"C:\\demo.pptx\" -Duration 60",
  "timeout": 120000
}
```

### 远程下发接口

POST `/worker/scripts` 可远程下发脚本，无需重启 Worker：

```json
{
  "name": "play_ppt.ps1",
  "content": "param([string]$FilePath)...",
  "version": "20260418-120000",
  "overwrite": true
}
```