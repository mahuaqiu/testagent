# 项目说明（供 AI Agent 阅读）

这是一个 **Python 多端自动化测试工程**，覆盖 Web、App、API 三端。

## 技术栈

- 语言: Python 3.10+
- 框架: pytest
- Web UI: Playwright
- App UI: Appium
- API: requests
- 数据: Faker
- 报告: allure

## 目录结构

```
autotest/
├── common/                  # 公共库（生成用例时优先搜索此目录）
│   ├── base_api.py          # HTTP 请求基类，所有 API Service 继承它
│   ├── assertions.py        # 自定义断言函数（assert_status_ok, assert_json_contains 等）
│   ├── config.py            # 配置管理，读取 config/settings.yaml
│   ├── data_factory.py      # 测试数据工厂，基于 Faker 生成随机数据
│   ├── db.py                # 数据库操作封装
│   └── utils.py             # 通用工具（retry 装饰器、timestamp、wait_until）
│
├── web/                     # Web 端（Playwright）
│   ├── pages/               # Page Object 层 —— 单个页面的元素操作
│   │   ├── base_page.py     # Web BasePage（所有 Web 页面的父类）
│   │   └── login_page.py    # 示例页面
│   ├── steps/               # Steps 层 —— 跨页面/多步骤业务流程封装
│   │   └── login_steps.py   # 登录业务流程（打开页面+填写+提交+断言）
│   ├── tests/               # 测试用例
│   │   └── test_login.py    # 示例用例
│   └── conftest.py          # Web 端 fixtures（browser, page, login_steps）
│
├── app/                     # App 端（Appium）
│   ├── pages/               # Page Object 层
│   │   ├── base_page.py     # App BasePage（所有 App 页面的父类）
│   │   └── login_page.py    # 示例页面
│   ├── steps/               # Steps 层
│   │   └── login_steps.py   # 登录业务流程
│   ├── tests/               # 测试用例
│   │   └── test_login.py    # 示例用例
│   └── conftest.py          # App 端 fixtures（driver, login_steps）
│
├── api/                     # 接口测试
│   ├── services/            # Service 层（单接口封装）
│   │   └── user_service.py  # 示例 Service
│   ├── steps/               # Steps 层（多接口组合的业务流程）
│   │   └── auth_steps.py    # 认证流程（登录+获取token+注入）
│   ├── tests/               # 测试用例
│   │   └── test_user_api.py # 示例用例
│   └── conftest.py          # API 端 fixtures（user_service, auth_steps）
│
├── config/
│   └── settings.yaml        # 多环境配置文件
├── data/                    # 测试数据 / 截图存放
├── conftest.py              # 全局 fixtures（config, data_factory）
└── pyproject.toml           # 依赖管理
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
- App 用例必须加 `@pytest.mark.app`
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

## 公共库使用指南

### 测试数据（data_factory fixture）
```python
def test_example(self, data_factory):
    user = data_factory.random_user()        # 随机用户数据
    user = data_factory.template_user(name="固定名")  # 部分固定
    phone = data_factory.random_phone()      # 随机手机号
```

### Steps（fixture 注入使用）
```python
# Web 端
def test_home_page(self, login_steps, page):
    login_steps.login_as("user", "pass")     # 一行完成登录
    # 后续测试首页功能...

# API 端
def test_create_order(self, auth_steps, order_service):
    auth_steps.login_and_set_token(order_service)  # 一行完成认证
    resp = order_service.create_order(...)
```

### 重试（retry 装饰器）
```python
from common.utils import retry

@retry(max_attempts=3, delay=2)
def flaky_step():
    ...
```

### 数据库（db fixture 需自行在 conftest 中添加）
```python
from common.db import DB
db = DB(host="...", port=3306, user="...", password="...", database="...")
rows = db.query("SELECT * FROM users WHERE phone = %s", ("13800001111",))
```

## 运行测试

```bash
# 安装依赖
pip install -e ".[all]"

# 运行全部
pytest

# 只运行 Web 端
pytest web/tests/ -m web

# 只运行 API 端
pytest api/tests/ -m api

# 只运行 App 端
pytest app/tests/ -m app

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
