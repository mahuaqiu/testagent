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
- **OCR 动作**：`ocr_click`, `ocr_input`, `ocr_wait`, `ocr_assert`, `ocr_get_text`
- **图像动作**：`image_click`, `image_wait`, `image_assert`
- **坐标动作**：`click`, `swipe`, `input`, `press`
- **其他**：`screenshot`, `wait`, `start_app`, `stop_app`
- **Web 特有**：`navigate`

动作参数：`value`(必填)、`offset`(可选偏移)、`timeout`(默认5000ms)、`threshold`(图像匹配默认0.8)。

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