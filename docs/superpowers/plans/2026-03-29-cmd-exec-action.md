# cmd_exec Action 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 cmd_exec 动作，允许在宿主机执行 shell/cmd 命令

**Architecture:** 新增 CmdExecAction 执行器，注册到 ActionRegistry；扩展 ActionResult 添加 exit_code/stdout/stderr 字段；更新 API 文档

**Tech Stack:** Python subprocess, dataclass

---

## 文件结构

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/task/result.py` | 修改 | ActionResult 添加 exit_code/stdout/stderr |
| `worker/task/action.py` | 修改 | ActionType 添加 CMD_EXEC |
| `worker/actions/cmd_exec.py` | 新增 | CmdExecAction 执行器 |
| `worker/actions/__init__.py` | 修改 | 注册 CmdExecAction |
| `worker/platforms/base.py` | 修改 | BASE_SUPPORTED_ACTIONS 添加 cmd_exec |
| `api.yaml` | 修改 | 更新 API 文档 |

---

### Task 1: 扩展 ActionResult 数据结构

**Files:**
- Modify: `worker/task/result.py:33-74`

- [ ] **Step 1: 添加新字段到 ActionResult**

在 `@dataclass` 定义中添加三个新字段：

```python
@dataclass
class ActionResult:
    """单个动作执行结果。"""

    number: int  # 动作在任务列表中的序号（第几个动作）
    action_type: str
    status: ActionStatus
    duration_ms: int = 0
    output: Optional[str] = None
    error: Optional[str] = None
    screenshot: Optional[str] = None  # base64 或文件路径
    context: Any = None  # 执行后更新的 context（如 start_app 后返回新 page）
    # cmd_exec 专用字段
    exit_code: Optional[int] = None  # 命令退出码
    stdout: Optional[str] = None     # 标准输出
    stderr: Optional[str] = None     # 标准错误
```

- [ ] **Step 2: 更新 from_dict 方法**

在 `from_dict` 方法中添加新字段的解析：

```python
@classmethod
def from_dict(cls, data: Dict[str, Any]) -> "ActionResult":
    """从字典创建。"""
    return cls(
        number=data.get("number", 0),
        action_type=data.get("action_type", ""),
        status=ActionStatus(data.get("status", "pending")),
        duration_ms=data.get("duration_ms", 0),
        output=data.get("output"),
        error=data.get("error"),
        screenshot=data.get("screenshot"),
        context=data.get("context"),
        exit_code=data.get("exit_code"),
        stdout=data.get("stdout"),
        stderr=data.get("stderr"),
    )
```

- [ ] **Step 3: 更新 to_dict 方法**

在 `to_dict` 方法中添加新字段的输出：

```python
def to_dict(self) -> Dict[str, Any]:
    """转换为字典。"""
    result = {
        "number": self.number,
        "action_type": self.action_type,
        "status": self.status.value,
        "duration_ms": self.duration_ms,
    }
    if self.output is not None:
        result["output"] = self.output
    if self.error is not None:
        result["error"] = self.error
    if self.screenshot is not None:
        result["screenshot"] = self.screenshot
    if self.exit_code is not None:
        result["exit_code"] = self.exit_code
    if self.stdout is not None:
        result["stdout"] = self.stdout
    if self.stderr is not None:
        result["stderr"] = self.stderr
    # context 不需要序列化到结果中
    return result
```

- [ ] **Step 4: 验证语法正确**

运行: `python -c "from worker.task.result import ActionResult; print('OK')"`
预期输出: `OK`

- [ ] **Step 5: 提交**

```bash
git add worker/task/result.py
git commit -m "feat: ActionResult 添加 exit_code/stdout/stderr 字段"
```

---

### Task 2: 添加 CMD_EXEC 动作类型

**Files:**
- Modify: `worker/task/action.py:12-43`

- [ ] **Step 1: 在 ActionType 枚举中添加 CMD_EXEC**

在 `ActionType` 枚举类的末尾添加：

```python
class ActionType(Enum):
    """动作类型枚举。"""

    # ... 现有动作保持不变 ...

    # 应用操作
    START_APP = "start_app"          # 启动应用
    STOP_APP = "stop_app"            # 关闭应用

    # 命令执行
    CMD_EXEC = "cmd_exec"            # 执行宿主机命令
```

- [ ] **Step 2: 验证语法正确**

运行: `python -c "from worker.task.action import ActionType; print(ActionType.CMD_EXEC.value)"`
预期输出: `cmd_exec`

- [ ] **Step 3: 提交**

```bash
git add worker/task/action.py
git commit -m "feat: ActionType 添加 CMD_EXEC"
```

---

### Task 3: 实现 CmdExecAction 执行器

**Files:**
- Create: `worker/actions/cmd_exec.py`

- [ ] **Step 1: 创建执行器文件**

创建 `worker/actions/cmd_exec.py`，内容如下：

```python
"""
命令执行动作执行器。

在宿主机上执行 shell/cmd 命令。
"""

import logging
import subprocess
from typing import TYPE_CHECKING

from worker.actions.base import ActionExecutor
from worker.task import Action, ActionResult, ActionStatus

if TYPE_CHECKING:
    from worker.platforms.base import PlatformManager

logger = logging.getLogger(__name__)


class CmdExecAction(ActionExecutor):
    """
    命令执行动作。

    在宿主机上执行 shell/cmd 命令，所有平台均支持。
    命令始终在 Worker 所在机器执行，与目标设备无关。
    """

    name = "cmd_exec"
    requires_context = False  # 不需要执行上下文（浏览器/设备）
    requires_ocr = False

    def execute(
        self, platform: "PlatformManager", action: Action, context=None
    ) -> ActionResult:
        """
        执行命令。

        Args:
            platform: 平台管理器（此动作不依赖平台能力）
            action: 动作参数，value 字段存放命令字符串
            context: 执行上下文（不使用）

        Returns:
            ActionResult: 包含 exit_code、stdout、stderr
        """
        cmd = action.value
        if not cmd:
            return ActionResult(
                number=0,
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error="command is required (use 'value' field)",
            )

        timeout_ms = action.timeout or 30000
        timeout_sec = timeout_ms / 1000

        logger.info(f"Executing command: {cmd[:100]}{'...' if len(cmd) > 100 else ''}")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )

            status = ActionStatus.SUCCESS if result.returncode == 0 else ActionStatus.FAILED

            logger.info(f"Command completed: exit_code={result.returncode}")

            return ActionResult(
                number=0,
                action_type="cmd_exec",
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output=f"Command executed: {cmd[:50]}{'...' if len(cmd) > 50 else ''}",
                error=result.stderr if result.returncode != 0 else None,
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"Command timeout after {timeout_ms}ms: {cmd[:50]}")
            return ActionResult(
                number=0,
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error=f"Command timeout after {timeout_ms}ms",
            )

        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ActionResult(
                number=0,
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error=str(e),
            )
```

- [ ] **Step 2: 验证语法正确**

运行: `python -c "from worker.actions.cmd_exec import CmdExecAction; print(CmdExecAction.name)"`
预期输出: `cmd_exec`

- [ ] **Step 3: 提交**

```bash
git add worker/actions/cmd_exec.py
git commit -m "feat: 新增 CmdExecAction 执行器"
```

---

### Task 4: 注册 CmdExecAction 到 ActionRegistry

**Files:**
- Modify: `worker/actions/__init__.py`

- [ ] **Step 1: 导入 CmdExecAction**

在导入部分添加：

```python
# 导入所有 Action 执行器
from worker.actions.ocr import (
    OcrClickAction,
    OcrInputAction,
    OcrWaitAction,
    OcrAssertAction,
    OcrGetTextAction,
    OcrPasteAction,
)
from worker.actions.image import (
    ImageClickAction,
    ImageWaitAction,
    ImageAssertAction,
    ImageClickNearTextAction,
)
from worker.actions.coordinate import (
    ClickAction,
    InputAction,
    SwipeAction,
    PressAction,
    ScreenshotAction,
    WaitAction,
)
from worker.actions.cmd_exec import CmdExecAction  # 新增
```

- [ ] **Step 2: 在注册函数中注册**

在 `_register_all_actions` 函数中添加：

```python
def _register_all_actions():
    """注册所有 Action 执行器。"""
    # OCR Actions
    ActionRegistry.register(OcrClickAction())
    ActionRegistry.register(OcrInputAction())
    ActionRegistry.register(OcrWaitAction())
    ActionRegistry.register(OcrAssertAction())
    ActionRegistry.register(OcrGetTextAction())
    ActionRegistry.register(OcrPasteAction())

    # Image Actions
    ActionRegistry.register(ImageClickAction())
    ActionRegistry.register(ImageWaitAction())
    ActionRegistry.register(ImageAssertAction())
    ActionRegistry.register(ImageClickNearTextAction())

    # Coordinate Actions
    ActionRegistry.register(ClickAction())
    ActionRegistry.register(InputAction())
    ActionRegistry.register(SwipeAction())
    ActionRegistry.register(PressAction())
    ActionRegistry.register(ScreenshotAction())
    ActionRegistry.register(WaitAction())

    # Command Actions
    ActionRegistry.register(CmdExecAction())  # 新增
```

- [ ] **Step 3: 在 __all__ 中添加**

```python
__all__ = [
    "ActionExecutor",
    "BaseActionExecutor",
    "ActionRegistry",
    "ActionResult",
    "ActionStatus",
    # OCR Actions
    "OcrClickAction",
    "OcrInputAction",
    "OcrWaitAction",
    "OcrAssertAction",
    "OcrGetTextAction",
    "OcrPasteAction",
    # Image Actions
    "ImageClickAction",
    "ImageWaitAction",
    "ImageAssertAction",
    "ImageClickNearTextAction",
    # Coordinate Actions
    "ClickAction",
    "InputAction",
    "SwipeAction",
    "PressAction",
    "ScreenshotAction",
    "WaitAction",
    # Command Actions
    "CmdExecAction",  # 新增
]
```

- [ ] **Step 4: 验证注册成功**

运行: `python -c "from worker.actions import ActionRegistry; print('cmd_exec' in ActionRegistry.list_all())"`
预期输出: `True`

- [ ] **Step 5: 提交**

```bash
git add worker/actions/__init__.py
git commit -m "feat: 注册 CmdExecAction 到 ActionRegistry"
```

---

### Task 5: 更新 BASE_SUPPORTED_ACTIONS

**Files:**
- Modify: `worker/platforms/base.py:30-35`

- [ ] **Step 1: 添加 cmd_exec 到通用动作列表**

修改 `BASE_SUPPORTED_ACTIONS`：

```python
class PlatformManager(ABC):
    """
    平台管理器抽象基类。

    所有平台执行引擎都需要继承此类并实现抽象方法。
    基于 OCR/图像识别定位，不依赖传统元素选择器。
    """

    # 通用动作列表（所有平台支持）
    BASE_SUPPORTED_ACTIONS: Set[str] = {
        "ocr_click", "ocr_input", "ocr_wait", "ocr_assert", "ocr_get_text", "ocr_paste",
        "image_click", "image_wait", "image_assert", "image_click_near_text",
        "click", "swipe", "input", "press", "screenshot", "wait",
        "cmd_exec",  # 新增：执行宿主机命令
    }
```

- [ ] **Step 2: 验证语法正确**

运行: `python -c "from worker.platforms.base import PlatformManager; print('cmd_exec' in PlatformManager.BASE_SUPPORTED_ACTIONS)"`
预期输出: `True`

- [ ] **Step 3: 提交**

```bash
git add worker/platforms/base.py
git commit -m "feat: BASE_SUPPORTED_ACTIONS 添加 cmd_exec"
```

---

### Task 6: 更新 API 文档

**Files:**
- Modify: `api.yaml`

- [ ] **Step 1: 在 Action action_type enum 中添加 cmd_exec**

在 `Action` schema 的 `action_type` enum 中添加 `cmd_exec`：

```yaml
action_type:
  type: string
  enum:
    - ocr_click
    - ocr_input
    - ocr_wait
    - ocr_assert
    - ocr_get_text
    - ocr_paste
    - image_click
    - image_wait
    - image_assert
    - image_click_near_text
    - click
    - swipe
    - input
    - press
    - screenshot
    - wait
    - start_app
    - stop_app
    - navigate
    - cmd_exec  # 新增
```

- [ ] **Step 2: 在 action_type description 中添加说明**

在 description 中添加 cmd_exec 的说明：

```yaml
description: |
  动作类型，分为以下几类：

  【OCR 文字识别动作】
  - ocr_click: 点击屏幕上识别到的文字
  - ocr_input: 在识别到的文字附近输入文本
  - ocr_wait: 等待指定文字出现
  - ocr_assert: 断言指定文字存在
  - ocr_get_text: 获取屏幕上所有文字
  - ocr_paste: OCR 定位后粘贴剪贴板内容

  【图像识别动作】
  - image_click: 点击匹配到的图像位置
  - image_wait: 等待指定图像出现
  - image_assert: 断言指定图像存在
  - image_click_near_text: 点击文本附近最近的图像

  【坐标动作】
  - click: 点击指定坐标
  - swipe: 滑动手势
  - input: 在指定坐标输入文本

  【其他动作】
  - press: 按键操作
  - screenshot: 截图
  - wait: 固定等待
  - start_app: 启动应用/浏览器
  - stop_app: 关闭应用/浏览器
  - navigate: 跳转 URL（Web 专用）
  - cmd_exec: 执行宿主机命令（所有平台支持）
```

- [ ] **Step 3: 在 value description 中添加 cmd_exec 说明**

```yaml
value:
  type: string
  description: |
    动作核心值，根据 action_type 不同含义不同：
    - ocr_click/ocr_input/ocr_wait/ocr_assert/ocr_paste: 要识别的文字内容
    - image_click_near_text: 要查找的目标文字
    - click/input: 此字段不使用
    - swipe: 此字段不使用
    - screenshot: 截图名称/标识
    - wait: 等待的毫秒数，如 "1000"
    - start_app: 应用包名或浏览器类型
      - Web: "chromium"、"firefox"、"webkit"
      - Android: 应用包名，如 "com.example.app"
      - iOS: Bundle ID，如 "com.example.app"
    - stop_app: 应用包名（可选，不填则关闭当前应用）
    - navigate: 要跳转的 URL
    - cmd_exec: 要执行的命令字符串，如 "echo hello"、"dir C:\\Users"
```

- [ ] **Step 4: 在 ActionResult schema 中添加新字段**

在 `ActionResult` schema 的 properties 中添加：

```yaml
ActionResult:
  type: object
  properties:
    number:
      type: integer
      description: "动作序号（在任务列表中的位置，从 0 开始）"
    action_type:
      type: string
      description: "动作类型"
    status:
      type: string
      enum: [pending, running, success, failed, skipped]
      description: "动作状态"
    duration_ms:
      type: integer
      description: "执行耗时(ms)"
    output:
      type: string
      description: "输出内容"
    error:
      type: string
      description: "错误信息"
    screenshot:
      type: string
      description: "截图（base64 或路径）"
    exit_code:
      type: integer
      description: "命令退出码（仅 cmd_exec 动作返回），0 表示成功"
    stdout:
      type: string
      description: "命令标准输出（仅 cmd_exec 动作返回）"
    stderr:
      type: string
      description: "命令标准错误输出（仅 cmd_exec 动作返回）"
```

- [ ] **Step 5: 在示例部分添加 cmd_exec 使用示例**

在文件末尾的示例部分添加：

```yaml
# cmd_exec - 执行宿主机命令
# 示例 1: 执行简单命令
# {"action_type": "cmd_exec", "value": "echo hello"}

# 示例 2: 执行带超时的命令
# {"action_type": "cmd_exec", "value": "python script.py", "timeout": 60000}

# 示例 3: Windows 列出目录
# {"action_type": "cmd_exec", "value": "dir C:\\Users"}

# 示例 4: Mac/Linux 查看文件
# {"action_type": "cmd_exec", "value": "ls -la /tmp"}
```

- [ ] **Step 6: 提交**

```bash
git add api.yaml
git commit -m "docs: API 文档添加 cmd_exec 动作说明"
```

---

### Task 7: 验证完整功能

- [ ] **Step 1: 启动 Worker（如果已配置 OCR 服务）**

运行: `cd /Users/ma/Documents/autotest && source .venv/bin/activate && python -m worker.main`

（如果 OCR 服务未配置，Worker 可能无法完全启动，但可以验证代码加载）

- [ ] **Step 2: 验证动作注册**

运行: `python -c "
from worker.actions import ActionRegistry
print('Registered actions:')
for action in sorted(ActionRegistry.list_all()):
    print(f'  - {action}')
"`
预期输出包含 `cmd_exec`

- [ ] **Step 3: 手动测试执行器（可选）**

运行简单的命令测试：
```python
from worker.actions.cmd_exec import CmdExecAction
from worker.task import Action
from worker.config import PlatformConfig

# 创建一个假的 platform 用于测试
class MockPlatform:
    pass

action = Action(action_type="cmd_exec", value="echo hello")
executor = CmdExecAction()
result = executor.execute(MockPlatform(), action)
print(f"Status: {result.status}")
print(f"Exit code: {result.exit_code}")
print(f"Stdout: {result.stdout}")
```

预期输出: Status=SUCCESS, Exit code=0, Stdout="hello\n"

---

### Task 8: 最终提交整合

- [ ] **Step 1: 检查所有变更已提交**

运行: `git status`
预期: 无未提交的变更

- [ ] **Step 2: 推送到远程（如果需要）**

```bash
git push origin main
```

---

## 实现顺序总结

1. Task 1: ActionResult 添加字段（数据结构基础）
2. Task 2: ActionType 添加 CMD_EXEC（类型定义）
3. Task 3: 创建 CmdExecAction 执行器（核心逻辑）
4. Task 4: 注册到 ActionRegistry（使动作可用）
5. Task 5: 更新 BASE_SUPPORTED_ACTIONS（平台支持声明）
6. Task 6: 更新 API 文档（对外说明）
7. Task 7: 验证功能
8. Task 8: 最终提交