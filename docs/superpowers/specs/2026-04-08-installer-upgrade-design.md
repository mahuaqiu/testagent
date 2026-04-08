# Worker 安装包与远程升级系统设计文档

## 概述

本文档描述 Test Worker 的安装包制作方案和远程升级机制设计。

**目标**：
1. 打包成可分发的 EXE 安装包，支持图形界面安装和命令行静默安装
2. 支持从平台下发升级指令，Worker 自动下载更新并重启

## 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        整体架构                                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────┐     ┌─────────────────┐                       │
│  │   安装程序      │     │   平台服务      │                       │
│  │  installer.exe  │     │ platform_api    │                       │
│  │                 │     │                 │                       │
│  │ - 图形界面安装  │────▶│ - 下发升级指令 │                       │
│  │ - 命令行静默安装│     │ - 提供下载地址 │                       │
│  │ - 写入配置      │     │                 │                       │
│  │ - 安装后启动    │     └─────────────────┘                       │
│  └─────────────────┘              │                                │
│         │                         │ POST /worker/upgrade           │
│         │ 安装                     │                                │
│         ▼                         ▼                                │
│  ┌─────────────────────────────────────────────────┐               │
│  │              Worker 服务                         │               │
│  │  test-worker.exe                                │               │
│  │                                                 │               │
│  │  ┌─────────────┐  ┌─────────────┐              │               │
│  │  │ HTTP Server │  │ 升级模块    │              │               │
│  │  │             │  │             │              │               │
│  │  │/task/execute│  │- 下载安装包 │              │               │
│  │  │/task/...    │  │- 静默安装   │              │               │
│  │  │/worker/upgrade│ │- 重启服务   │              │               │
│  │  └─────────────┘  └─────────────┘              │               │
│  │                                                 │               │
│  │  config/worker.yaml ← 安装时写入，升级时保留    │               │
│  └─────────────────────────────────────────────────┘               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 一、安装程序设计

### 1.1 技术选型

**选择 Inno Setup 6**，理由：
- 免费、轻量、界面美观
- 脚本驱动配置，适合快速开发
- 原生支持静默安装（`/VERYSILENT`）
- 支持自定义命令行参数

### 1.2 安装界面

```
┌──────────────────────────────────────────────────────────────────┐
│                    Test Worker 安装向导                           │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  第 1 步：选择安装位置                                           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ 安装目录:                                                   │ │
│  │ [C:\Program Files\Test Worker                    ] [浏览]   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  第 2 步：配置 Worker 参数                                       │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ IP 地址:         [192.168.1.100          ] (自动获取本机IP) │ │
│  │ 服务端口:        [8088                  ]                   │ │
│  │ 命名空间:        [meeting_public         ]                   │ │
│  │ 平台 API 地址:   [http://192.168.0.102:8000              ]   │ │
│  │ OCR 服务地址:    [http://192.168.0.102:9021              ]   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ☑ 创建桌面快捷方式                                              │
│  ☑ 安装完成后启动 Worker                                         │
│                                                                  │
│                        [上一步]  [安装]  [取消]                   │
└──────────────────────────────────────────────────────────────────┘
```

### 1.3 配置项默认值来源

| 配置项 | 默认值来源 | 说明 |
|--------|------------|------|
| IP 地址 | 自动获取本机 IP | Inno Setup Pascal 脚本调用 Winsock API |
| 端口 | `config/worker.yaml` 默认值 | 默认 8088 |
| 命名空间 | `config/worker.yaml` 默认值 | 默认 meeting_public |
| 平台 API | `config/worker.yaml` 默认值 | 默认 http://192.168.0.102:8000 |
| OCR 服务 | `config/worker.yaml` 默认值 | 默认 http://192.168.0.102:9021 |

### 1.4 命令行参数支持

安装包支持以下命令行参数：

| 参数 | 说明 | 示例 |
|------|------|------|
| `/VERYSILENT` | 完全静默安装，无任何界面 | `installer.exe /VERYSILENT` |
| `/SUPPRESSMSGBOXES` | 抑制消息框 | `/VERYSILENT /SUPPRESSMSGBOXES` |
| `/DIR="path"` | 指定安装目录 | `/DIR="D:\TestWorker"` |
| `/IP="x.x.x.x"` | Worker IP 地址 | `/IP="192.168.1.100"` |
| `/PORT="xxxx"` | 服务端口 | `/PORT="8088"` |
| `/NAMESPACE="xxx"` | 命名空间 | `/NAMESPACE="meeting_public"` |
| `/PLATFORM_API="url"` | 平台 API 地址 | `/PLATFORM_API="http://192.168.0.102:8000"` |
| `/OCR_SERVICE="url"` | OCR 服务地址 | `/OCR_SERVICE="http://192.168.0.102:9021"` |

**静默安装示例**：
```bash
test-worker-installer.exe /VERYSILENT /SUPPRESSMSGBOXES \
  /IP="192.168.1.100" \
  /PORT="8088" \
  /NAMESPACE="meeting_public" \
  /PLATFORM_API="http://192.168.0.102:8000" \
  /OCR_SERVICE="http://192.168.0.102:9021"
```

### 1.5 配置保留机制

升级安装时保留用户配置：

- `[Files]` 段对 config 目录使用 `Flags: onlyifdoesntexist`
- 检测已安装版本时跳过配置输入页面
- 仅新安装时写入 config/worker.yaml

### 1.6 文件结构

```
installer/
├── installer.iss           # Inno Setup 脚本
├── build_installer.ps1     # 构建脚本
└── assets/
    └── icon.ico            # 安装包图标（可选）
```

## 二、升级机制设计

### 2.1 升级流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                      升级流程                                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  平台服务                           Worker 服务                     │
│     │                                   │                          │
│     │  POST /worker/upgrade            │                          │
│     │  {download_url, version}         │                          │
│     │─────────────────────────────────▶│                          │
│     │                                   │                          │
│     │                                   │ 1. 版本校验               │
│     │                                   │    if version == 当前版本 │
│     │                                   │    → 返回 "无需升级"      │
│     │                                   │                          │
│     │  [版本一致，无需升级]             │                          │
│     │◀──────────────────────────────────│                          │
│     │                                   │                          │
│     │  [版本不一致，继续升级]           │                          │
│     │                                   │                          │
│     │                                   │ 2. 记录升级状态           │
│     │                                   │    (写入 upgrade.json)    │
│     │                                   │                          │
│     │                                   │ 3. 下载安装包             │
│     │                                   │    → temp/installer.exe   │
│     │                                   │                          │
│     │                                   │ 4. 验证下载完整性         │
│     │                                   │                          │
│     │                                   │ 5. 启动静默安装           │
│     │                                   │                          │
│     │                                   │ 6. Worker 立即退出        │
│     │                                   │                          │
│     │                                   ▼                          │
│     │                          ┌─────────────────┐                 │
│     │                          │  Inno Setup     │                 │
│     │                          │  安装程序运行   │                 │
│     │                          │                 │                 │
│     │                          │ - 替换文件      │                 │
│     │                          │ - 保留 config   │                 │
│     │                          │ - 启动 Worker   │                 │
│     │                          └─────────────────┘                 │
│     │                                   │                          │
│     │  Worker 重新上线                  │                          │
│     │◀──────────────────────────────────│                          │
│     │                                   │                          │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 HTTP 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/worker/upgrade` | POST | 接收升级指令 |

**请求格式**：
```json
{
  "version": "20260408150000",
  "download_url": "http://192.168.0.102:8000/downloads/test-worker-installer.exe",
  "force": true
}
```

**响应格式**：

版本一致，无需升级：
```json
{
  "status": "skipped",
  "message": "当前版本已是最新，无需升级",
  "current_version": "20260408150000",
  "target_version": "20260408150000"
}
```

开始升级：
```json
{
  "status": "upgrading",
  "message": "Worker 正在升级，预计 30 秒后恢复",
  "current_version": "20260405120000",
  "target_version": "20260408150000"
}
```

### 2.3 任务处理策略

收到升级指令时，如果正在执行任务：
- **直接中断**：强制终止当前任务，立即升级
- 任务结果不返回，平台通过 Worker 重连判断升级完成

### 2.4 升级模块文件结构

```
worker/upgrade/
├── __init__.py          # 模块导出
├── models.py            # 升级请求/响应模型
├── handler.py           # HTTP 接口处理
├── downloader.py        # 安装包下载
├── installer.py         # 静默安装执行
└── state.py             # 升级状态管理
```

### 2.5 升级状态文件

用于记录升级过程，方便异常恢复：

```json
{
  "status": "upgrading",
  "target_version": "20260408150000",
  "current_version": "20260405120000",
  "download_url": "...",
  "started_at": "2026-04-08T15:00:00",
  "completed_at": null,
  "error": null
}
```

## 三、构建流程

### 3.1 构建步骤

```
步骤 1: build_windows.ps1
    → PyInstaller 打包
    → 输出: dist/test-worker/

步骤 2: build_installer.ps1
    → Inno Setup 编译
    → 输入: dist/test-worker/
    → 输出: dist/test-worker-installer.exe
```

### 3.2 最终产物

| 文件 | 说明 |
|------|------|
| `dist/test-worker/` | 可直接运行的 Worker 目录（开发/测试用） |
| `dist/test-worker-installer.exe` | 安装包（分发部署用） |

## 四、实现要点

### 4.1 版本校验逻辑

```python
current_version = get_current_version()
if request.version and request.version == current_version:
    return UpgradeResponse(
        status="skipped",
        message="当前版本已是最新，无需升级",
        ...
    )
```

### 4.2 静默安装命令

```python
cmd = [
    installer_path,
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    f"/DIR=\"{install_dir}\"",
]
subprocess.Popen(cmd, shell=True)
```

### 4.3 配置写入逻辑

Inno Setup Pascal 脚本在 `CurStepChanged` 钩子中写入配置：

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not IsUpgradeInstall() then
    begin
      // 仅新安装时写入配置
      SaveStringToFile(ConfigFile, ConfigContent, False);
    end;
  end;
end;
```

## 五、风险与注意事项

1. **下载失败**：需要合理的超时和重试机制，记录失败状态
2. **安装失败**：Worker 退出后安装程序未启动成功，需要人工介入
3. **版本回退**：不支持自动回退，需要手动重新安装旧版本
4. **并发升级**：多个 Worker 同时升级时，需要平台控制下载带宽

## 六、后续扩展

1. **签名校验**：安装包添加数字签名，升级时验证签名
2. **增量更新**：大版本时下载完整包，小版本时下载增量包
3. **升级窗口**：支持指定升级时间窗口（如凌晨空闲时段）
4. **心跳检测**：平台通过心跳判断 Worker 升级是否成功