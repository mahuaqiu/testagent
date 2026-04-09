# Web 平台页面切换与关闭功能设计

**日期**: 2026-04-09
**状态**: Draft

## 背景

当前 Web 平台不支持多页面（标签页/窗口）管理。当网页通过 `window.open()` 打开新窗口后，截图和操作仍然停留在原页面，无法切换到新页面操作。

## 目标

新增两个 Web 专属动作：
1. `switched_page` - 切换到指定页面
2. `close_page` - 关闭当前页面并自动切换焦点

## 设计

### 动作定义

#### switched_page

| 参数 | 类型 | 说明 |
|------|------|------|
| `action_type` | string | `"switched_page"` |
| `value` | string/int | 页面索引（从 1 开始），如 "1", "2", "3" |

**行为**：
- 切换到指定索引的页面（按打开顺序，从 1 开始）
- 索引超出当前页面数量时报错
- 成功后更新 `_current_page` 和 `_sessions["default"]["page"]`

#### close_page

| 参数 | 类型 | 说明 |
|------|------|------|
| `action_type` | string | `"close_page"` |

**行为**：
- 关闭 `_current_page`（当前所在的页面）
- 关闭后自动切换到浏览器当前显示的页面（Playwright 检测到的焦点页面）
- 不允许关闭最后一个页面

### 实现方案

在 `worker/platforms/web.py` 中直接处理，符合现有 web 专属 action 模式。

#### 改动点

1. **SUPPORTED_ACTIONS**：添加 `"switched_page"` 和 `"close_page"`

2. **execute_action**：添加 case 分支
   ```python
   elif action.action_type == "switched_page":
       result = self._action_switched_page(action)
   elif action.action_type == "close_page":
       result = self._action_close_page(action)
   ```

3. **新增私有方法**：
   - `_action_switched_page(action: Action) -> ActionResult`
   - `_action_close_page(action: Action) -> ActionResult`
   - `_get_page_index(page: Page) -> int`（辅助方法）

#### 核心逻辑

**switched_page**：
```python
def _action_switched_page(self, action: Action) -> ActionResult:
    # 1. 验证浏览器上下文
    if not self._browser_context:
        return error("Browser context not available")

    # 2. 解析索引
    index = int(action.value)  # 用户传 1, 2, 3

    # 3. 获取有效页面列表（过滤已关闭的）
    pages = [p for p in self._browser_context.pages if not p.is_closed()]

    # 4. 验证范围
    if index < 1 or index > len(pages):
        return error("Page index {index} out of range, only {len(pages)} pages available")

    # 5. 切换
    self._current_page = pages[index - 1]
    self._sessions["default"]["page"] = pages[index - 1]
    return success(f"Switched to page {index}")
```

**close_page**：
```python
def _action_close_page(self, action: Action) -> ActionResult:
    # 1. 验证浏览器上下文
    if not self._browser_context:
        return error("Browser context not available")

    # 2. 验证当前页面
    if not self._current_page:
        return error("No active page to close")

    # 3. 获取有效页面列表
    pages = [p for p in self._browser_context.pages if not p.is_closed()]

    # 4. 不允许关闭最后一页
    if len(pages) <= 1:
        return error("Cannot close the last page")

    # 5. 关闭当前页面
    _run_async(self._current_page.close())

    # 6. 刷新列表，找到新的焦点页面
    pages = [p for p in self._browser_context.pages if not p.is_closed()]
    new_page = pages[0]  # 浏览器会自动切换焦点，取第一个有效页面

    # 7. 更新状态
    self._current_page = new_page
    self._sessions["default"]["page"] = new_page
    return success(f"Closed page, now on page {self._get_page_index(new_page)}")
```

### 错误处理

| 场景 | 动作 | 错误信息 |
|------|------|----------|
| value 为空 | switched_page | `"Page index is required"` |
| value 无效 | switched_page | `"Invalid page index: {value}"` |
| 索引超范围 | switched_page | `"Page index {value} out of range, only {n} pages available"` |
| 无浏览器上下文 | both | `"Browser context not available"` |
| 只剩最后页 | close_page | `"Cannot close the last page"` |
| 无当前页面 | close_page | `"No active page to close"` |

### 边界情况

- **页面被外部关闭**：每次操作前重新获取 `pages` 并过滤 `is_closed()`
- **页面列表动态变化**：不缓存页面列表，每次调用时重新查询

## 范围限制

- 这两个动作仅适用于 Web 平台
- 其他平台（Android/iOS/Mac/Windows）不需要实现

## 测试要点

1. 基础切换：打开多页面后切换
2. 边界索引：切换到最后一个页面
3. 无效索引：超出范围报错
4. 关闭中间页：关闭后自动切换
5. 关闭最后一页：报错拒绝
6. 页面被外部关闭：自动过滤已关闭页面