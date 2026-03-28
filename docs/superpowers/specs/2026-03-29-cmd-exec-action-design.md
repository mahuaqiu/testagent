---
name: cmd_exec action
description: 在宿主机执行 shell/cmd 命令的动作
type: project
---

# cmd_exec Action 设计文档

## 概述

新增 `cmd_exec` 动作，用于在宿主机（Worker 所在机器）执行 shell/cmd 命令。所有平台（Windows/Mac/Web/Android/iOS）均支持，命令始终在宿主机执行。

## Why

自动化测试场景中经常需要在宿主机执行命令，例如：
- 启动/停止本地服务
- 文件操作（复制、删除、重命名）
- 调用外部工具或脚本
- 系统状态检查

**How to apply**: 当测试用例需要宿主机命令执行能力时，使用 `cmd_exec` 动作。

## 架构设计

### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `worker/actions/cmd_exec.py` | 新增 | CmdExecAction 执行器 |
| `worker/actions/__init__.py` | 修改 | 注册 CmdExecAction |
| `worker/task/action.py` | 修改 | ActionType 添加 CMD_EXEC |
| `worker/task/result.py` | 修改 | ActionResult 添加 exit_code/stdout/stderr |
| `worker/platforms/base.py` | 修改 | BASE_SUPPORTED_ACTIONS 添加 cmd_exec |
| `api.yaml` | 修改 | 更新 API 文档 |

### 执行流程

```
Task → Platform.execute_action() → ActionRegistry.get("cmd_exec") → CmdExecAction.execute() → subprocess → ActionResult
```

## 数据结构

### Action 参数

使用现有 `value` 字段存放命令字符串：

```json
{
  "action_type": "cmd_exec",
  "value": "echo hello",
  "timeout": 30000
}
```

### ActionResult 扩展

新增三个字段：

```python
@dataclass
class ActionResult:
    # ... 现有字段 ...
    exit_code: Optional[int] = None      # 命令退出码
    stdout: Optional[str] = None         # 标准输出
    stderr: Optional[str] = None         # 标准错误
```

## 执行器实现

### 核心逻辑

```python
import subprocess
import logging
from worker.actions.base import ActionExecutor
from worker.task import Action, ActionResult, ActionStatus

logger = logging.getLogger(__name__)

class CmdExecAction(ActionExecutor):
    """命令执行动作。在宿主机上执行 shell/cmd 命令。"""

    name = "cmd_exec"
    requires_context = False
    requires_ocr = False

    def execute(self, platform, action: Action, context=None) -> ActionResult:
        cmd = action.value
        if not cmd:
            return ActionResult(
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error="command is required (use 'value' field)"
            )

        timeout_ms = action.timeout or 30000
        timeout_sec = timeout_ms / 1000

        logger.info(f"Executing command: {cmd[:100]}...")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec
            )

            status = ActionStatus.SUCCESS if result.returncode == 0 else ActionStatus.FAILED

            logger.info(f"Command completed: exit_code={result.returncode}")

            return ActionResult(
                action_type="cmd_exec",
                status=status,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                output=f"Command executed: {cmd[:50]}..." if len(cmd) > 50 else f"Command executed: {cmd}",
                error=result.stderr if result.returncode != 0 else None
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"Command timeout after {timeout_ms}ms")
            return ActionResult(
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error=f"Command timeout after {timeout_ms}ms"
            )
        except Exception as e:
            logger.error(f"Command execution failed: {e}")
            return ActionResult(
                action_type="cmd_exec",
                status=ActionStatus.FAILED,
                error=str(e)
            )
```

### 设计要点

1. `shell=True`: 支持 Windows cmd 和 Mac/Linux shell，包括管道、多命令等复杂语法
2. `capture_output=True`: 同时捕获 stdout 和 stderr
3. `text=True`: 输出为字符串而非 bytes
4. 超时使用 `action.timeout`（默认 30 秒）
5. `requires_context=False`: 不依赖浏览器/设备上下文

## 错误处理

| 场景 | 返回结果 |
|------|----------|
| 命令为空 | FAILED, error="command is required" |
| 命令超时 | FAILED, error="Command timeout after Xms" |
| 命令不存在 | FAILED, exit_code=非零, stderr=错误信息 |
| 命令执行失败 | FAILED, exit_code=非零, stdout/stderr=实际输出 |
| 命令执行成功 | SUCCESS, exit_code=0, stdout=实际输出 |

## API 文档更新

### Action action_type 新增

```yaml
enum:
  # ... 现有动作 ...
  - cmd_exec
```

### Action value 描述更新

```yaml
value:
  description: |
    - cmd_exec: 要执行的命令字符串，如 "dir C:\\Users" 或 "ls -la"
```

### ActionResult 新增字段

```yaml
ActionResult:
  properties:
    # ... 现有字段 ...
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

### 使用示例

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

## 平台支持

所有平台自动支持，无需修改平台管理器代码：

| 平台 | 执行位置 |
|------|----------|
| Windows | Windows 宿主机 |
| Mac | Mac 宿主机 |
| Web | Worker 所在机器（Windows 或 Mac） |
| Android | Worker 所在机器（Windows 或 Mac） |
| iOS | Worker 所在机器（Windows 或 Mac） |

## 测试验证

可通过 HTTP API 调用验证：

```bash
curl -X POST http://localhost:8080/task/execute \
  -H "Content-Type: application/json" \
  -d '{"platform": "web", "actions": [{"action_type": "cmd_exec", "value": "echo hello"}]}'
```

预期响应包含：
```json
{
  "status": "success",
  "actions": [{
    "action_type": "cmd_exec",
    "status": "success",
    "exit_code": 0,
    "stdout": "hello\n",
    "stderr": ""
  }]
}
```