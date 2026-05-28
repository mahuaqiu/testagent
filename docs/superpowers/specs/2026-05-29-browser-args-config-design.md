# Web 平台 Chromium 启动参数配置支持

## 概述

为 Web 平台添加可配置的 Chromium 启动参数支持，允许用户通过配置文件自定义浏览器行为（如禁用翻译功能），方便后续扩展其他启动参数。

## 背景

当前 Chromium 启动参数在 `worker/platforms/web.py` 中硬编码，只有 `--hide-crash-restore-bubble` 和代理相关参数。用户无法通过配置文件添加自定义启动参数（如 `--disable-features=Translate,TranslateUI` 禁用翻译功能）。

## 设计方案

### 配置结构

采用字符串列表方式，简洁直观：

```yaml
platforms:
  web:
    browser_args:
      - "--disable-features=Translate,TranslateUI"
```

### 涉及文件

1. **配置文件** `config/worker.yaml`
   - 新增 `platforms.web.browser_args` 字段（字符串列表）

2. **配置类** `worker/config.py`
   - `PlatformConfig` dataclass 新增 `browser_args: List[str]` 字段
   - `from_dict` 方法新增读取逻辑

3. **Web 平台实现** `worker/platforms/web.py`
   - `WebPlatformManager.__init__` 新增 `self.browser_args` 属性
   - `_async_start` 方法合并默认参数和配置参数

### 参数合并策略

- **默认参数**（硬编码）：`--hide-crash-restore-bubble` 等核心参数保持不变
- **用户参数**（配置文件）：追加到默认参数后面
- 合并顺序确保核心功能稳定，同时允许用户扩展

## 实现步骤

1. 修改 `worker/config.py`：`PlatformConfig` 新增 `browser_args` 字段
2. 修改 `worker/platforms/web.py`：读取并合并启动参数
3. 修改 `config/worker.yaml`：添加示例配置（注释说明）

## 测试验证

- 启动 Worker，确认浏览器启动成功
- 检查浏览器翻译功能是否被禁用（Chrome 菜单中无翻译选项）
- 验证其他参数也能正确传递

## 示例配置

```yaml
platforms:
  web:
    enabled: null
    headless: false
    browser_type: Chromium
    timeout: 30000
    session_timeout: 300
    screenshot_dir: data/screenshots
    ignore_https_errors: true
    user_data_dir: data/chrome_profile
    permissions:
      - camera
      - microphone
      - clipboard-read
      - clipboard-write
    clear_profile_on_start: true
    request_blacklist:
      - pattern: "uba.js"
        action: "404"
    token_headers:
      - "X-Auth-Token"
    # Chromium 启动参数（字符串列表）
    browser_args:
      - "--disable-features=Translate,TranslateUI"
```

## 常用 Chromium 启动参数参考

| 参数 | 说明 |
|------|------|
| `--disable-features=Translate,TranslateUI` | 禁用浏览器翻译功能 |
| `--disable-infobars` | 禁用信息栏（部分版本已失效） |
| `--disable-extensions` | 禁用扩展 |
| `--start-maximized` | 启动时最大化窗口 |
| `--disable-gpu` | 禁用 GPU 加速（某些环境需要） |