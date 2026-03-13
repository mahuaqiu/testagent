# 项目说明（供 AI Agent 阅读）

这是一个 **Python 多端自动化测试工程**，覆盖 Web、Android、iOS、Windows、API 五端。

## 技术栈

- 语言: Python 3.10+
- 框架: pytest
- Web UI: Playwright
- Android UI: Appium (UiAutomator2)
- iOS UI: Appium (XCUITest)
- Windows 桌面: WinAppDriver / Playwright
- API: requests
- 数据: Faker
- 报告: allure

## 目录结构

```
autotest/
├── common/                  # 公共库（生成用例时优先搜索此目录）
│   ├── base_api.py          # HTTP 请求基类，所有 API Service 继承它
│   ├── assertions.py        # 自定义断言函数
│   ├── config.py            # 配置管理，读取 config/settings.yaml
│   ├── data_factory.py      # 测试数据工厂
│   ├── db.py                # 数据库操作封装
│   └── utils.py             # 通用工具（retry、timestamp、wait_until）
│
├── web/                     # Web 端（Playwright）
│   ├── pages/               # Page Object 层
│   ├── steps/               # Steps 层
│   ├── tests/               # 测试用例
│   ├── remote/              # Remote Worker 模块（CDP 远程执行）
│   │   ├── browser.py       # CDP 浏览器管理
│   │   ├── session.py       # 会话管理（用户隔离）
│   │   ├── page.py          # 页面操作封装
│   │   ├── task.py          # 任务模型和队列
│   │   ├── worker.py        # Worker 主服务
│   │   └── server.py        # HTTP API 服务
│   └── conftest.py          # Web 端 fixtures
│
├── android/                 # Android 端（Appium + UiAutomator2）
│   ├── pages/
│   ├── steps/
│   ├── tests/
│   └── conftest.py
│
├── ios/                     # iOS 端（Appium + XCUITest）
│   ├── pages/
│   ├── steps/
│   ├── tests/
│   └── conftest.py
│
├── windows/                 # Windows 桌面端
│   ├── pages/
│   ├── steps/
│   ├── tests/
│   └── conftest.py
│
├── api/                     # 接口测试
│   ├── services/            # Service 层
│   ├── steps/               # Steps 层
│   ├── tests/               # 测试用例
│   └── conftest.py
│
├── config/
│   └── settings.yaml        # 多环境配置文件
├── data/                    # 测试数据 / 截图存放
├── conftest.py              # 全局 fixtures
└── pyproject.toml           # 依赖管理
```

## Remote Worker 模式

本项目作为 **Test Worker** 运行，可被外部 pytest agent 远程调度执行测试任务。

### 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│           外部 pytest-agent（调度方）                        │
│                   HTTP API / CDP                            │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│           本项目 Test Worker（执行方）                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ web/remote/                                          │   │
│  │  ├── Worker        主服务，接收任务、管理会话         │   │
│  │  ├── RemoteBrowser CDP 浏览器管理                   │   │
│  │  ├── RemotePage    页面操作封装                     │   │
│  │  └── server.py     HTTP API 服务                    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 启动 Worker 服务

```bash
# Web 端 Worker（默认端口 8080，CDP 端口 9222）
python -m web.remote.server --port 8080 --cdp-port 9222

# 有头模式（方便观察）
python -m web.remote.server --no-headless

# 连接远程浏览器
python -m web.remote.server --cdp-endpoint ws://remote-host:9222
```

### HTTP API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/status` | GET | 服务状态 |
| `/cdp-endpoint` | GET | 获取 CDP 端点 |
| `/task` | POST | 提交任务到队列 |
| `/task/execute` | POST | 提交并立即执行任务 |
| `/result/{task_id}` | GET | 获取任务结果 |
| `/session` | POST | 创建会话 |
| `/session/{id}` | DELETE | 关闭会话 |

### 任务执行示例

```python
import requests

# 提交并执行任务
response = requests.post("http://localhost:8080/task/execute", json={
    "user_id": "user_001",
    "actions": [
        {"action_type": "navigate", "value": "https://example.com"},
        {"action_type": "fill", "selector": "input[name='username']", "value": "test"},
        {"action_type": "click", "selector": "button[type='submit']"},
        {"action_type": "screenshot", "value": "result"}
    ]
})

result = response.json()
print(f"Status: {result['status']}")
print(f"Duration: {result['duration_ms']}ms")
```

## 三层架构（重要）

本项目采用 **Pages/Services → Steps → Tests** 三层架构：

```
┌─────────────────────────────────────────────────────┐
│  tests/          测试用例层 —— 组合 steps 验证场景    │
├─────────────────────────────────────────────────────┤
│  steps/          业务流程层 —— 封装多步骤业务动作      │
├─────────────────────────────────────────────────────┤
│  pages/services/ 操作层 —— 单页面/单接口的基础操作     │
├─────────────────────────────────────────────────────┤
│  common/         公共库 —— 断言、数据、配置、工具      │
└─────────────────────────────────────────────────────┘
```

### 什么时候用 Page/Service，什么时候用 Steps

| 场景 | 用哪层 | 举例 |
|------|--------|------|
| 测试登录页面本身的各种输入校验 | 直接用 `LoginPage` | `test_login_wrong_password` |
| 登录只是前置步骤，重点测后续功能 | 用 `LoginSteps` | `login_steps.login_as(...)` 然后测首页 |
| 测试单个接口的入参校验 | 直接用 `UserService` | `test_create_user_missing_name` |
| 需要先登录获取 token 再测其他接口 | 用 `AuthSteps` | `auth_steps.login_and_set_token(svc)` |
| 一个业务流程涉及多个页面跳转 | 封装为 `XxxSteps` | 下单流程: 搜索→加购→结算→支付 |

### Steps 层设计原则

1. **一个 Steps 类对应一个业务域**：如 `LoginSteps`, `OrderSteps`, `SearchSteps`
2. **Steps 内部组合多个 Page/Service**：自己不直接操作元素/发请求
3. **Steps 方法是完整的业务动作**：调用方无需了解内部细节
4. **Steps 可以互相组合**：如 `OrderSteps` 内部使用 `LoginSteps`
5. **Steps 注册为 fixture**：在 conftest.py 中提供，用例直接注入使用

## 命名规范

| 类型 | 命名模式 | 示例 |
|------|----------|------|
| 测试文件 | `test_<功能>.py` | `test_login.py`, `test_user_api.py` |
| 测试类 | `Test<功能>` | `TestLogin`, `TestUserAPI` |
| 测试函数 | `test_<场景描述>` | `test_login_success`, `test_login_wrong_password` |
| Page 类 | `<页面名>Page` | `LoginPage`, `HomePage` |
| Page 文件 | `<页面名>_page.py` | `login_page.py`, `home_page.py` |
| Service 类 | `<模块名>Service` | `UserService`, `OrderService` |
| Service 文件 | `<模块名>_service.py` | `user_service.py`, `order_service.py` |
| Steps 类 | `<业务>Steps` | `LoginSteps`, `OrderSteps`, `AuthSteps` |
| Steps 文件 | `<业务>_steps.py` | `login_steps.py`, `order_steps.py` |
| 定位器属性 | `loc_<用途>` | `loc_username`, `loc_submit_btn` |
| fixture | 小写下划线 | `page`, `driver`, `login_steps`, `auth_steps` |

## 编写测试用例规则

### 标记（markers）
- Web 用例必须加 `@pytest.mark.web`
- Android 用例必须加 `@pytest.mark.android`
- iOS 用例必须加 `@pytest.mark.ios`
- Windows 用例必须加 `@pytest.mark.windows`
- API 用例必须加 `@pytest.mark.api`
- 冒烟用例额外加 `@pytest.mark.smoke`

### 用例结构
1. **准备**: 通过 Steps 完成前置操作（如登录），或初始化 Page/Service
2. **执行**: 调用业务操作方法
3. **断言**: 使用 Page 的 `should_*` 方法或 `common.assertions` 中的函数

### API 用例断言
优先使用 `common.assertions` 中的函数:
- `assert_status_ok(resp)` — 断言 200
- `assert_status(resp, 201)` — 断言指定状态码
- `assert_json_contains(resp, {"code": 0})` — 断言 JSON 包含
- `assert_json_key_exists(resp, "id", "name")` — 断言 key 存在
- `assert_list_not_empty(resp, "data")` — 断言列表非空

### Web/App 用例断言
使用 Page Object 中的 `should_*` 方法封装断言逻辑。

## 配置管理

配置文件 `config/settings.yaml` 按端分组：

```yaml
dev:
  web:
    base_url: "https://dev.example.com"
    remote:
      cdp_port: 9222
      headless: false     # 默认有头模式，方便观察

  android:
    appium_server: "http://127.0.0.1:4723"
    desired_caps:
      platformName: "Android"
      automationName: "UiAutomator2"

  ios:
    appium_server: "http://127.0.0.1:4724"
    desired_caps:
      platformName: "iOS"
      automationName: "XCUITest"

  windows:
    app_path: "/path/to/app.exe"

  db:
    host: "127.0.0.1"
```

## 运行测试

```bash
# 安装依赖
pip install -e ".[all]"

# 运行全部
pytest

# 只运行 Web 端
pytest web/tests/ -m web

# 只运行 Android 端
pytest android/tests/ -m android

# 只运行 iOS 端
pytest ios/tests/ -m ios

# 只运行 Windows 端
pytest windows/tests/ -m windows

# 只运行 API 端
pytest api/tests/ -m api

# 冒烟测试
pytest -m smoke

# 指定环境
ENV=staging pytest
```

## AI 用例生成流水线

本项目配置了 SKILL 组合 + Agent 编排的用例生成流水线：

```
/gen-testcase <需求描述>
    │
    ▼
testcase-leader (Agent 编排)
    ├── 阶段1: testcase-refiner  → 需求结构化（与用户确认）
    ├── 阶段2: testcase-planner  → 扫描代码库 + 生成计划（用户确认）
    └── 阶段3: testcase-coder    → 按计划生成代码
```

| 组件 | 类型 | 位置 | 职责 |
|------|------|------|------|
| gen-testcase | Skill (入口) | `.qoder/skills/gen-testcase/` | 统一入口，启动 leader |
| testcase-leader | Agent (编排) | `.qoder/agents/testcase-leader.md` | 串联调度三个 Skill |
| testcase-refiner | Skill | `.qoder/skills/testcase-refiner/` | 手工用例 → 结构化测试步骤 |
| testcase-planner | Skill | `.qoder/skills/testcase-planner/` | 代码库分析 → 生成计划 |
| testcase-coder | Skill | `.qoder/skills/testcase-coder/` | 按计划生成代码文件 |

也可以单独调用某个 Skill：
- `/testcase-refiner` — 只做需求结构化
- `/testcase-planner` — 只做代码库分析
- `/testcase-coder` — 给定计划直接生成代码