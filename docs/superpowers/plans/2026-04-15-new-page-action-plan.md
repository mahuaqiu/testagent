# new_page Action 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Web 平台新增 new_page 动作，创建空白新标签页并自动切换焦点

**Architecture:** 在 worker/platforms/web.py 中实现，与现有 switched_page/close_page 模式一致，直接添加 SUPPORTED_ACTIONS、execute_action case、_action_new_page 方法

**Tech Stack:** Python, Playwright, FastAPI

---

## 文件结构

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `worker/platforms/web.py` | 修改 | 添加 SUPPORTED_ACTIONS、execute_action case、_action_new_page 方法 |
| `CLAUDE.md` | 修改 | 文档新增 new_page 动作说明 |

---

### Task 1: 更新 SUPPORTED_ACTIONS

**Files:**
- Modify: `worker/platforms/web.py:87`

- [ ] **Step 1: 修改 SUPPORTED_ACTIONS 添加 "new_page"**

```python
# 修改前
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token", "switched_page", "close_page"}

# 修改后
SUPPORTED_ACTIONS: Set[str] = {"navigate", "start_app", "stop_app", "get_token", "new_page", "switched_page", "close_page"}
```

- [ ] **Step 2: 验证修改正确**

运行: `python -c "from worker.platforms.web import WebPlatformManager; print('new_page' in WebPlatformManager.SUPPORTED_ACTIONS)"`
预期输出: `True`

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/web.py
git commit -m "feat(web): SUPPORTED_ACTIONS 添加 new_page"
```

---

### Task 2: 添加 execute_action case 分支

**Files:**
- Modify: `worker/platforms/web.py:673-682`

- [ ] **Step 1: 找到 execute_action 中的 navigate case**

使用 Grep 搜索: `execute_action.*navigate` 或直接读取 web.py 找到 `_action_navigate` 调用位置

- [ ] **Step 2: 在 navigate 和 switched_page 之间添加 new_page case**

```python
elif action.action_type == "navigate":
    result = self._action_navigate(action, context)
elif action.action_type == "new_page":
    result = self._action_new_page(action)
elif action.action_type == "switched_page":
    result = self._action_switched_page(action)
elif action.action_type == "close_page":
    result = self._action_close_page(action)
```

- [ ] **Step 3: Commit**

```bash
git add worker/platforms/web.py
git commit -m "feat(web): execute_action 添加 new_page case"
```

---

### Task 3: 实现 _action_new_page 方法

**Files:**
- Modify: `worker/platforms/web.py` (新增方法，放在 _action_navigate 之后)

- [ ] **Step 1: 找到 _action_navigate 方法结束位置**

读取 web.py 找到 `_action_navigate` 方法的结束位置（约 line 927），在其后添加新方法

- [ ] **Step 2: 添加 _action_new_page 方法**

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

- [ ] **Step 3: 验证语法正确**

运行: `python -c "from worker.platforms.web import WebPlatformManager; print('OK')"`
预期输出: `OK`

- [ ] **Step 4: Commit**

```bash
git add worker/platforms/web.py
git commit -m "feat(web): 实现 _action_new_page 方法"
```

---

### Task 4: 更新 CLAUDE.md 文档

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 找到 Web 特有动作部分**

读取 CLAUDE.md 找到 "Web 特有" 动作列表的位置

- [ ] **Step 2: 在 Web 特有动作中添加 new_page**

在 `navigate`, `switched_page`, `close_page` 附近添加 `new_page` 的说明：

```markdown
- **Web 特有**：`navigate`, `new_page`, `switched_page`, `close_page`
```

同时在动作参数表格中添加 new_page 的参数说明（无额外参数，仅 action_type）。

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 新增 new_page 动作说明"
```

---

### Task 5: 功能验证

**Files:**
- 无文件改动，手动验证

- [ ] **Step 1: 启动 Worker 服务**

运行: `python -m worker.main`

- [ ] **Step 2: 发送测试请求**

使用 curl 或 HTTP 客户端发送包含 new_page 动作的请求，验证：
1. 新页面创建成功
2. 返回正确的页面索引
3. 后续操作在新页面上执行

测试请求示例：
```json
{
  "platform": "web",
  "actions": [
    {"action_type": "start_app"},
    {"action_type": "navigate", "value": "https://example.com"},
    {"action_type": "new_page"},
    {"action_type": "navigate", "value": "https://example.org"},
    {"action_type": "switched_page", "value": "1"},
    {"action_type": "close_page"}
  ]
}
```

- [ ] **Step 3: 确认页面管理闭环正常**

验证整个闭环流程：start_app → navigate → new_page → navigate → switched_page → close_page

---

## 完成标志

- `new_page` 在 SUPPORTED_ACTIONS 中
- execute_action 正确处理 new_page
- _action_new_page 方法实现完整
- CLAUDE.md 文档更新
- 功能验证通过