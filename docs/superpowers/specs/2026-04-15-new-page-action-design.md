# Web 平台 new_page 动作设计

**日期**: 2026-04-15
**状态**: Draft

## 背景

当前 Web 平台已有页面管理动作：
- `switched_page` - 切换到已有页面
- `close_page` - 关闭当前页面

但缺少创建新页面的能力。当需要在新标签页中操作时，只能依赖网页自身通过 `window.open()` 打开新窗口，无法主动控制。

## 目标

新增 `new_page` 动作：
- 创建空白新标签页
- 自动将焦点切换到新页面
- 后续截图、操作等全部在新页面上执行

形成完整的页面管理闭环：

| 动作 | 功能 |
|------|------|
| `new_page` | 创建新标签页，焦点转过去 |
| `navigate` | 在当前页面加载 URL |
| `switched_page` | 切换到已有页面 |
| `close_page` | 关闭当前页面，焦点自动转移 |

## 设计

### 动作定义

#### new_page

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action_type` | string | 是 | `"new_page"` |

**行为**：
1. 在当前浏览器上下文中创建新的空白标签页
2. 新页面自动成为当前活跃页面（更新 `_current_page`）
3. 后续所有操作（截图、点击等）都在新页面上执行
4. 返回新页面的索引号（从 1 开始，方便后续用 `switched_page` 切回）

**与其他动作配合**：
```
new_page → 创建空白新页面（索引=2）
navigate → 在新页面加载 URL
... 操作 ...
switched_page(1) → 切回原页面
```

### 索引机制

页面索引从 **1 开始**，按打开顺序排列：
- 只有一个页面时，索引是 **1**
- 打开第二个页面后，索引分别是 **1** 和 **2**
- 关闭页面后，剩余页面的索引会重新排列

`new_page` 创建的新页面会获得当前最大索引值。

### 实现方案

在 `worker/platforms/web.py` 中直接实现，与现有 `switched_page`、`close_page` 模式一致。

### 改动点

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `worker/platforms/web.py` | 修改 | 添加 SUPPORTED_ACTIONS、execute_action case、_action_new_page 方法 |
| `CLAUDE.md` | 修改 | 文档新增 new_page 动作说明 |

### 代码改动

#### 1. SUPPORTED_ACTIONS

```python
# 修改前（line 87）
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token", "switched_page", "close_page"}

# 修改后
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token", "new_page", "switched_page", "close_page"}
```

#### 2. execute_action case 分支

在 `execute_action` 方法中添加 case（约 line 679-682 附近）：

```python
elif action.action_type == "new_page":
    result = self._action_new_page(action)
elif action.action_type == "switched_page":
    result = self._action_switched_page(action)
elif action.action_type == "close_page":
    result = self._action_close_page(action)
```

#### 3. _action_new_page 方法

```python
def _action_new_page(self, action: Action) -> ActionResult:
    """创建新空白标签页并切换焦点。

    Returns:
        ActionResult: 包含新页面索引和上下文
    """
    # 验证浏览器上下文
    if not self._browser_context:
        return ActionResult(
            number=0,
            action_type="new_page",
            status=ActionStatus.FAILED,
            error="Browser context not available",
        )

    try:
        # 创建新空白页面
        new_page = _run_async(self._browser_context.new_page())
        new_page.set_default_timeout(self.timeout)

        # 更新当前页面引用
        self._current_page = new_page
        self._sessions["default"] = {
            "context": self._browser_context,
            "page": new_page,
        }

        # 获取新页面索引
        index = self._get_page_index(new_page)
        logger.info(f"Created new page {index}")

        return ActionResult(
            number=0,
            action_type="new_page",
            status=ActionStatus.SUCCESS,
            output=f"Created new page {index}",
            context=new_page,
        )
    except Exception as e:
        return ActionResult(
            number=0,
            action_type="new_page",
            status=ActionStatus.FAILED,
            error=f"Failed to create new page: {e}",
        )
```

### 错误处理

| 场景 | 错误信息 |
|------|----------|
| 无浏览器上下文 | `"Browser context not available"` |
| 创建页面失败 | `"Failed to create new page: {error}"` |

## 测试要点

1. **基础创建**：已有 1 个页面，调用 new_page 创建第 2 个页面
2. **索引返回**：确认返回的索引正确（新页面应为最大索引）
3. **焦点切换**：新页面创建后，截图和操作应在新页面上
4. **连续创建**：连续创建多个页面，索引依次递增
5. **配合 navigate**：new_page 后 navigate 加载 URL
6. **配合 switched_page**：创建后切回原页面验证
7. **无浏览器上下文**：start_app 未调用时调用 new_page 报错

### 页面管理闭环验证

```
start_app → 1 个页面（索引=1）
navigate → 加载 URL
new_page → 创建第 2 个空白页，焦点转到页面 2
navigate → 在页面 2 加载新 URL
switched_page(1) → 切回页面 1
switched_page(2) → 切回页面 2
close_page → 关闭页面 2，焦点自动转到页面 1
```

## 范围限制

- 此动作仅适用于 Web 平台
- 其他平台（Android/iOS/Mac/Windows）不需要实现