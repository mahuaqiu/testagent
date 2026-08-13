# 开机自动启动功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Worker 新增"开机自动启动"能力，覆盖安装器（默认勾上写注册表）和运行时设置窗口（开关同步注册表）两条入口，db 为唯一真相源。

**Architecture:** 新增 `common/autostart.py` 模块收口自启逻辑（db 读写 + 注册表读写 + 播种 + 同步），`schema_meta.auto_start` 为唯一真相源，`HKLM\...\Run` 注册表项为投影。安装器（NSIS）新增"选项页"承载设备发现 + 开机自启 checkbox；设置窗口加 checkbox 委托 autostart 模块；GUI 启动时播种。

**Tech Stack:** Python 3.12（`winreg` 内置）、PyQt5、NSIS、SQLite（`schema_meta` 表）。

## Global Constraints

- 不增加新依赖；注册表操作只用 Python 内置 `winreg`
- 注册表写 `HKLM\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `test-worker`，值数据为带引号的 exe 全路径
- 所有自启操作（db/注册表）失败只记 `logger.warning`，绝不抛异常中断主流程
- 非 Windows 平台 `winreg` 不可用，模块函数 no-op，`is_enabled()` 返回 False
- 自启状态只存 db `schema_meta`（key=`auto_start`，value=`true`/`false`），不读 `worker.yaml`
- 安装器：全新安装默认勾上写注册表，升级安装跳过自启，卸载删注册表键
- 注释和对话使用中文
- 提交时遵循现有提交信息风格（`feat:` / `fix:` 前缀，中文描述）

## File Structure

| 文件 | 责任 | 操作 |
|------|------|------|
| `common/autostart.py` | 自启逻辑收口：db 读写、注册表读写、播种、同步 | 新增 |
| `tests/common/test_autostart.py` | autostart 模块单元测试 | 新增 |
| `worker/gui_main.py` | GUI 入口，启动时调 `seed_from_registry()` | 修改 |
| `worker/settings_window.py` | 设置窗口加开机自启 checkbox，加载/保存委托 autostart | 修改 |
| `installer/installer.nsi` | 新增选项页，设备发现迁移 + 开机自启 checkbox，安装段写注册表，卸载删注册表 | 修改 |
| `tests/test_installer_config.py` | 安装器 NSIS 脚本断言测试 | 修改 |

---

### Task 1: autostart 模块 db 层 + is_enabled

**Files:**
- Create: `common/autostart.py`
- Test: `tests/common/test_autostart.py`

**Interfaces:**
- Produces: `common.autostart.is_enabled() -> bool`，读 `schema_meta.auto_start`，无 key 返回 False
- Produces: `common.autostart._db_path() -> Path`（模块内部，返回 `get_base_dir()/data/worker.db`，可被测试 monkeypatch）
- Produces: `common.autostart._get_db() -> Database`（模块内部，临时短连接）
- Produces: `common.autostart._read_db_flag() -> Optional[str]`（模块内部，读 `auto_start` 值，无返回 None）
- Produces: `common.autostart._write_db_flag(value: bool) -> None`（模块内部，写 `auto_start`）

- [ ] **Step 1: 写失败测试 - is_enabled 无 key 返回 False**

创建 `tests/common/__init__.py`（空文件）和 `tests/common/test_autostart.py`：

```python
"""开机自启模块测试。"""

import sys
from pathlib import Path

import pytest

from worker.storage.database import Database


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """让 autostart 模块使用临时目录的 db。"""
    db_path = tmp_path / "worker.db"
    # 初始化 schema_meta 表
    Database(db_path).connection().close()
    import common.autostart as autostart
    monkeypatch.setattr(autostart, "_db_path", lambda: db_path)
    return db_path


def test_is_enabled_returns_false_when_no_flag(isolated_db):
    """db 无 auto_start key 时返回 False。"""
    import common.autostart as autostart

    assert autostart.is_enabled() is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/common/test_autostart.py::test_is_enabled_returns_false_when_no_flag -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'common.autostart'`

- [ ] **Step 3: 实现 autostart 模块 db 层 + is_enabled**

创建 `common/autostart.py`：

```python
"""开机自启模块。

db 的 schema_meta.auto_start 是自启状态的唯一真相源，
HKLM\\...\\Run 注册表项是它的投影。所有操作失败只记日志，不抛异常。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 注册表 Run 键路径（系统级，所有用户登录生效）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "test-worker"
AUTO_START_KEY = "auto_start"

# 非 Windows 平台 winreg 不可用，模块仍可安全 import
if sys.platform == "win32":
    import winreg
else:
    winreg = None


def _db_path() -> Path:
    """返回 worker.db 路径（可被测试 monkeypatch）。"""
    from common.packaging import get_base_dir
    return Path(get_base_dir()) / "data" / "worker.db"


def _get_db():
    """获取临时 db 连接工厂（短连接，低频读写）。"""
    from worker.storage.database import Database
    return Database(_db_path())


def _read_db_flag() -> Optional[str]:
    """读 schema_meta.auto_start，无 key 返回 None。失败返回 None。"""
    try:
        db = _get_db()
        conn = db.connection()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (AUTO_START_KEY,)
        ).fetchone()
        return row["value"] if row else None
    except Exception as e:
        logger.warning(f"读取自启标志失败: {e}")
        return None


def _write_db_flag(value: bool) -> None:
    """写 schema_meta.auto_start。失败只记日志。"""
    try:
        db = _get_db()
        conn = db.connection()
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
            (AUTO_START_KEY, "true" if value else "false"),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"写入自启标志失败: {e}")


def is_enabled() -> bool:
    """读 db 的 schema_meta.auto_start，返回当前自启意愿。

    db 无该 key 时返回 False（未初始化视为关闭）。
    非 Windows 平台返回 False。
    """
    if winreg is None:
        return False
    flag = _read_db_flag()
    return flag == "true"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/common/test_autostart.py::test_is_enabled_returns_false_when_no_flag -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add common/autostart.py tests/common/__init__.py tests/common/test_autostart.py
git commit -m "feat(autostart): 新增自启模块 db 层与 is_enabled"
```

---

### Task 2: autostart 注册表层 + set_enabled

**Files:**
- Modify: `common/autostart.py`
- Test: `tests/common/test_autostart.py`

**Interfaces:**
- Consumes: Task 1 的 `_write_db_flag`、`is_enabled`、`winreg`、`RUN_KEY`、`VALUE_NAME`
- Produces: `common.autostart.set_enabled(enabled: bool) -> None`，先写 db 再同步注册表（写/删 HKLM Run 值），失败只记日志
- Produces: `common.autostart._write_registry()`（模块内部，写 HKLM Run 值）
- Produces: `common.autostart._delete_registry()`（模块内部，删 HKLM Run 值）
- Produces: `common.autostart._registry_has_value() -> bool`（模块内部，查询注册表有无值，供播种用）

- [ ] **Step 1: 写失败测试 - set_enabled(True) 写 db 为 true**

追加到 `tests/common/test_autostart.py`：

```python
def test_set_enabled_true_writes_db_flag_true(isolated_db):
    """set_enabled(True) 应把 db 标志写为 true。"""
    import common.autostart as autostart

    autostart.set_enabled(True)

    assert autostart._read_db_flag() == "true"
    assert autostart.is_enabled() is True


def test_set_enabled_false_writes_db_flag_false(isolated_db):
    """set_enabled(False) 应把 db 标志写为 false。"""
    import common.autostart as autostart

    autostart.set_enabled(False)

    assert autostart._read_db_flag() == "false"
    assert autostart.is_enabled() is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/common/test_autostart.py -k set_enabled -v`
Expected: FAIL with `AttributeError: module 'common.autostart' has no attribute 'set_enabled'`

- [ ] **Step 3: 实现注册表层 + set_enabled**

在 `common/autostart.py` 末尾追加：

```python
def _exe_path() -> str:
    """返回自启要执行的 exe 全路径（打包后即 test-worker.exe）。"""
    return sys.executable


def _write_registry() -> None:
    """写 HKLM Run 值，数据为带引号的 exe 路径。失败只记日志。"""
    if winreg is None:
        return
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"'
            )
        finally:
            key.Close()
    except Exception as e:
        logger.warning(f"写入自启注册表失败: {e}")


def _delete_registry() -> None:
    """删 HKLM Run 值。键不存在视为成功。失败只记日志。"""
    if winreg is None:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        finally:
            key.Close()
    except FileNotFoundError:
        # 值不存在视为成功
        pass
    except Exception as e:
        logger.warning(f"删除自启注册表失败: {e}")


def _registry_has_value() -> bool:
    """查询 HKLM Run 是否存在 test-worker 值。失败返回 False。"""
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
        finally:
            key.Close()
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"查询自启注册表失败: {e}")
        return False


def set_enabled(enabled: bool) -> None:
    """设置自启状态：先写 db，再同步注册表。

    enabled=True→写 HKLM Run 键；enabled=False→删 Run 键。
    注册表操作失败不抛异常（只记日志），db 已更新成功即视为设置成功。
    """
    _write_db_flag(enabled)
    if enabled:
        _write_registry()
    else:
        _delete_registry()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/common/test_autostart.py -k set_enabled -v`
Expected: PASS（db 层断言通过；注册表层在测试环境因 HKLM 权限或 winreg 不可用会记 warning 但不抛异常）

- [ ] **Step 5: 提交**

```bash
git add common/autostart.py tests/common/test_autostart.py
git commit -m "feat(autostart): 新增注册表层与 set_enabled 同步逻辑"
```

---

### Task 3: autostart 播种 seed_from_registry

**Files:**
- Modify: `common/autostart.py`
- Test: `tests/common/test_autostart.py`

**Interfaces:**
- Consumes: Task 1 的 `_write_db_flag`、`_read_db_flag`；Task 2 的 `_registry_has_value`
- Produces: `common.autostart.seed_from_registry() -> None`，db 无 key 时读注册表初始化，有则跳过，幂等不抛异常

- [ ] **Step 1: 写失败测试 - db 无 key 且注册表有值时播种为 true**

追加到 `tests/common/test_autostart.py`：

```python
def test_seed_writes_true_when_registry_has_value_and_db_empty(isolated_db, monkeypatch):
    """db 无 key 且注册表有值时，播种应写 db 为 true。"""
    import common.autostart as autostart

    monkeypatch.setattr(autostart, "_registry_has_value", lambda: True)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "true"
    assert autostart.is_enabled() is True


def test_seed_writes_false_when_registry_empty_and_db_empty(isolated_db, monkeypatch):
    """db 无 key 且注册表无值时，播种应写 db 为 false。"""
    import common.autostart as autostart

    monkeypatch.setattr(autostart, "_registry_has_value", lambda: False)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "false"
    assert autostart.is_enabled() is False


def test_seed_idempotent_when_db_already_set(isolated_db, monkeypatch):
    """db 已有 key 时，播种不应改写（即使注册表状态不同）。"""
    import common.autostart as autostart

    autostart._write_db_flag(False)
    # 注册表显示有值，但 db 已设为 false，播种不应覆盖
    monkeypatch.setattr(autostart, "_registry_has_value", lambda: True)

    autostart.seed_from_registry()

    assert autostart._read_db_flag() == "false"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/common/test_autostart.py -k seed -v`
Expected: FAIL with `AttributeError: module 'common.autostart' has no attribute 'seed_from_registry'`

- [ ] **Step 3: 实现 seed_from_registry**

在 `common/autostart.py` 末尾追加：

```python
def seed_from_registry() -> None:
    """首次启动播种：db 无 auto_start key 时，读注册表有无 test-worker
    值来初始化 db。已有则跳过。幂等、不抛异常（失败只记日志）。
    """
    if winreg is None:
        return
    try:
        if _read_db_flag() is not None:
            # db 已有标志，不覆盖
            return
        has_value = _registry_has_value()
        _write_db_flag(has_value)
        logger.info(f"自启状态已从注册表播种到 db: {has_value}")
    except Exception as e:
        logger.warning(f"播种自启状态失败: {e}")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/common/test_autostart.py -k seed -v`
Expected: PASS（3 个测试全部通过）

- [ ] **Step 5: 运行全部 autostart 测试确认无回归**

Run: `pytest tests/common/test_autostart.py -v`
Expected: PASS（所有测试通过）

- [ ] **Step 6: 提交**

```bash
git add common/autostart.py tests/common/test_autostart.py
git commit -m "feat(autostart): 新增 seed_from_registry 首次启动播种"
```

---

### Task 4: 安装器新增选项页与自启注册表写入

**Files:**
- Modify: `installer/installer.nsi`
- Test: `tests/test_installer_config.py`

**Interfaces:**
- Produces: NSIS 脚本中新增 `Var AutoStart`、`OptionsPageCreate`、`OptionsPageLeave` 函数、页面顺序新增选项页、安装段写注册表、卸载段删注册表

**说明:** NSIS 脚本无法在单元测试里编译运行，采用文本断言验证脚本结构（与现有 `test_installer_config.py` 范式一致）。

- [ ] **Step 1: 写失败测试 - 验证选项页与自启相关脚本结构**

追加到 `tests/test_installer_config.py`：

```python
def test_installer_has_autostart_variable_and_registry_logic() -> None:
    """安装脚本应包含开机自启变量、注册表写入（全新安装）、卸载删除逻辑。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    # 变量声明
    assert "Var AutoStart" in script

    # .onInit 默认勾上
    oninit_body = script.split("Function .onInit", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    assert "StrCpy $AutoStart ${BST_CHECKED}" in oninit_body

    # 安装段：全新安装写注册表，升级跳过
    main_section = script.split('Section "MainSection" SEC01', maxsplit=1)[1].split(
        "SectionEnd", maxsplit=1
    )[0]
    assert "skip_autostart" in main_section
    assert (
        'WriteRegStr HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "test-worker"'
        in main_section
    )
    assert 'StrCmp $IsUpgrade "1" skip_autostart' in main_section

    # 卸载段：删注册表
    uninstall_section = script.split("Section Uninstall", maxsplit=1)[1].split(
        "SectionEnd", maxsplit=1
    )[0]
    assert (
        'DeleteRegValue HKLM "Software\\Microsoft\\Windows\\CurrentVersion\\Run" "test-worker"'
        in uninstall_section
    )


def test_installer_has_options_page() -> None:
    """安装脚本应新增选项页，承载设备发现与开机自启。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")

    # 页面顺序：配置页后紧跟选项页
    assert "Page custom OptionsPageCreate OptionsPageLeave" in script
    # 选项页创建与离开函数
    assert "Function OptionsPageCreate" in script
    assert "Function OptionsPageLeave" in script
    # 选项页升级跳过
    options_create = script.split("Function OptionsPageCreate", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    assert 'StrCmp $IsUpgrade "1" skip_options' in options_create
    # 选项页含开机自启 checkbox
    assert '"开机自动启动"' in options_create


def test_installer_config_page_no_longer_has_device_discovery() -> None:
    """配置页应移除设备发现控件（迁至选项页）。"""
    script = INSTALLER_SCRIPT.read_text(encoding="utf-8")
    config_create = script.split("Function ConfigPageCreate", maxsplit=1)[1].split(
        "FunctionEnd", maxsplit=1
    )[0]
    # 配置页不再创建设备发现 checkbox
    assert "Device Discovery:" not in config_create
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_installer_config.py -k "autostart or options_page or no_longer_has_device_discovery" -v`
Expected: FAIL（脚本尚无相关内容）

- [ ] **Step 3: 修改变量声明，新增 AutoStart**

编辑 `installer/installer.nsi`，在 `Var DiscoverHarmonyPc` 之后（约第 48 行）新增：

```nsis
Var AutoStart
```

- [ ] **Step 4: 修改页面顺序，插入选项页**

编辑 `installer/installer.nsi`，把页面顺序段：

```nsis
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
```

改为：

```nsis
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom ConfigPageCreate ConfigPageLeave
Page custom OptionsPageCreate OptionsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
```

- [ ] **Step 5: 精简配置页，移除设备发现控件**

编辑 `ConfigPageCreate` 函数，删除"Row 5: Device discovery options"整段（从 `; Row 5: Device discovery options` 注释到 `Pop $DiscoverHarmonyPc`，约第 418-427 行）。配置页以 OCR 服务输入框结尾。

编辑 `ConfigPageLeave` 函数，删除读取设备发现的 4 行：
```nsis
  ${NSD_GetState} $DiscoverAndroid $DiscoverAndroid
  ${NSD_GetState} $DiscoverIos $DiscoverIos
  ${NSD_GetState} $DiscoverHarmonyMobile $DiscoverHarmonyMobile
  ${NSD_GetState} $DiscoverHarmonyPc $DiscoverHarmonyPc
```
保留 IP/端口/命名空间/平台 API/OCR 的读取。

- [ ] **Step 6: 新增 OptionsPageCreate 函数**

在 `ConfigPageLeave` 函数之后（`ReplaceConfigFile` 之前）新增：

```nsis
; 选项页创建：设备发现 + 开机自启
Function OptionsPageCreate
  ; 升级安装跳过（与配置页同模式）
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" skip_options

  !insertmacro MUI_HEADER_TEXT "Worker Options" "Select discovery and startup options"

  nsDialogs::Create 1018
  Pop $0

  ; Device Discovery（从配置页迁移，拆两行更宽松）
  ${NSD_CreateLabel} 0 0 100% 12u "Device Discovery:"
  ${NSD_CreateCheckbox} 0 18 80 12u "Android"
  Pop $DiscoverAndroid
  ${NSD_CreateCheckbox} 100 18 60 12u "iOS"
  Pop $DiscoverIos
  ${NSD_CreateCheckbox} 0 40 90 12u "Harmony Mobile"
  Pop $DiscoverHarmonyMobile
  ${NSD_CreateCheckbox} 110 40 80 12u "Harmony PC"
  Pop $DiscoverHarmonyPc

  ; 分隔线
  ${NSD_CreateHLine} 0 70 100% 1u ""
  Pop $0

  ; Startup
  ${NSD_CreateLabel} 0 88 100% 12u "Startup:"
  ${NSD_CreateCheckbox} 0 106 140 12u "开机自动启动"
  Pop $AutoStart

  ; 默认勾上（控件创建后同步状态）
  ${NSD_SetState} $AutoStart ${BST_CHECKED}

  nsDialogs::Show

  skip_options:
FunctionEnd

; 选项页离开：读取勾选状态
Function OptionsPageLeave
  Call IsUpgradeInstall
  StrCmp $IsUpgrade "1" done

  ; 静默安装没有控件，保留 .onInit 默认值
  IfSilent silent_install

  ${NSD_GetState} $DiscoverAndroid $DiscoverAndroid
  ${NSD_GetState} $DiscoverIos $DiscoverIos
  ${NSD_GetState} $DiscoverHarmonyMobile $DiscoverHarmonyMobile
  ${NSD_GetState} $DiscoverHarmonyPc $DiscoverHarmonyPc
  ${NSD_GetState} $AutoStart $AutoStart

  done:
  silent_install:
FunctionEnd
```

- [ ] **Step 7: .onInit 新增 AutoStart 默认值**

编辑 `.onInit` 函数，在 `StrCpy $DiscoverHarmonyPc ${BST_UNCHECKED}` 之后新增：

```nsis
  StrCpy $AutoStart ${BST_CHECKED}
```

- [ ] **Step 8: 安装段新增注册表写入**

编辑 `Section "MainSection" SEC01`，在 `CreateShortCut "$DESKTOP\..."` 之后、`WriteUninstaller` 之前新增：

```nsis
  ; 开机自启：仅全新安装处理，升级安装跳过
  StrCmp $IsUpgrade "1" skip_autostart
    StrCmp $AutoStart ${BST_CHECKED} 0 autostart_off
      ClearErrors
      WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Run" "test-worker" '"$INSTDIR\test-worker.exe"'
      IfErrors 0 autostart_done
        DetailPrint "Warning: 写入开机自启注册表失败，已跳过"
    autostart_off:
    autostart_done:
  skip_autostart:
```

- [ ] **Step 9: 卸载段新增注册表删除**

编辑 `Section Uninstall`，在 `Delete "$DESKTOP\${PRODUCT_NAME}.lnk"` 之前新增：

```nsis
  ; 清理开机自启注册表项
  DeleteRegValue HKLM "Software\Microsoft\Windows\CurrentVersion\Run" "test-worker"
```

- [ ] **Step 10: 运行测试确认通过**

Run: `pytest tests/test_installer_config.py -v`
Expected: PASS（原有测试 + 3 个新测试全部通过）

- [ ] **Step 11: 提交**

```bash
git add installer/installer.nsi tests/test_installer_config.py
git commit -m "feat(installer): 新增选项页承载设备发现与开机自启,安装写注册表卸载删"
```

---

### Task 5: 设置窗口加开机自启开关

**Files:**
- Modify: `worker/settings_window.py`
- Test: `tests/test_download_dialog.py` 旁手动验证（PyQt 控件无自动化测试基建，本任务以代码审查 + 手动验证为准）

**Interfaces:**
- Consumes: Task 1 的 `common.autostart.is_enabled`；Task 2 的 `common.autostart.set_enabled`

**说明:** 项目无 PyQt 控件自动化测试，沿用现有设置窗口的测试策略（手动验证 + 代码审查）。改动复用现有 checkbox 范式（`discover_*_checkbox`）。

- [ ] **Step 1: 调整窗口最小高度**

编辑 `worker/settings_window.py` 的 `_setup_ui`，把：

```python
        self.setMinimumHeight(480)
```

改为：

```python
        self.setMinimumHeight(540)
```

- [ ] **Step 2: 在设备发现行后新增分隔线 + 开机自启 checkbox**

编辑 `_setup_ui`，在 `grid.addLayout(discover_row, row, 0, 1, 3)` 与紧随其后的 `row += 1` 之后、`layout.addLayout(grid)` 之前插入（注意：原代码在 `grid.addLayout(discover_row, ...)` 后已有一行 `row += 1`，本步骤在其后插入，不要重复 `row += 1`）：

```python
        # 分隔线
        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        line3.setStyleSheet("background-color: #e8e8e8;")
        line3.setFixedHeight(1)
        grid.addWidget(line3, row, 0, 1, 3)
        row += 1

        # 开机自启开关
        grid.addWidget(self._create_label("开机自启"), row, 0)
        self.autostart_checkbox = QCheckBox("开机时自动启动 Worker")
        self.autostart_checkbox.setStyleSheet("font-size: 14px; color: #555555;")
        grid.addWidget(self.autostart_checkbox, row, 1)
        row += 1
```

插入后该区域应为：`grid.addLayout(discover_row, ...)` → `row += 1` → 分隔线块 → 开机自启块 → `layout.addLayout(grid)`。

- [ ] **Step 3: _load_values 读 db 显示状态**

编辑 `_load_values`，在 `self.discover_harmony_pc_checkbox.setChecked(discover_harmony_pc)` 之后新增：

```python
        # 开机自启
        from common.autostart import is_enabled
        self.autostart_checkbox.setChecked(is_enabled())
```

- [ ] **Step 4: _on_save 写 db + 同步注册表**

编辑 `_on_save`，在 `if not self._validate(): return` 之后、`original_content = ""` 之前新增：

```python
        # 开机自启：写 db + 同步注册表（独立于 worker.yaml）
        try:
            from common.autostart import set_enabled
            set_enabled(self.autostart_checkbox.isChecked())
        except Exception as e:
            logger.warning(f"保存开机自启设置失败: {e}")
            # 不阻断，继续保存其它配置
```

- [ ] **Step 5: 代码检查**

Run: `ruff check worker/settings_window.py`
Expected: 无错误（若有未使用导入等提示，按提示修正）

- [ ] **Step 6: 提交**

```bash
git add worker/settings_window.py
git commit -m "feat(settings): 设置窗口新增开机自启开关,委托 autostart 模块读写"
```

---

### Task 6: GUI 启动时播种自启状态

**Files:**
- Modify: `worker/gui_main.py`

**Interfaces:**
- Consumes: Task 3 的 `common.autostart.seed_from_registry`

- [ ] **Step 1: 在 Worker 启动后、托盘启动前加播种调用**

编辑 `worker/gui_main.py` 的 `GUIApp.run` 方法，找到：

```python
        # 启动 Worker
        self._splash.update_status("启动 Worker 服务...")
        self.app.processEvents()
        self._start_worker()

        # 启动托盘
        self._splash.update_status("启动系统托盘...")
```

在 `self._start_worker()` 之后、`# 启动托盘` 之前新增：

```python
        # 播种开机自启状态到 db（首次启动读注册表初始化，幂等）
        try:
            from common.autostart import seed_from_registry
            seed_from_registry()
        except Exception as e:
            logger.warning(f"播种开机自启状态失败: {e}")
```

- [ ] **Step 2: 代码检查**

Run: `ruff check worker/gui_main.py`
Expected: 无错误

- [ ] **Step 3: 提交**

```bash
git add worker/gui_main.py
git commit -m "feat(gui): Worker 启动后播种开机自启状态到 db"
```

---

### Task 7: 全量回归与手动验证

**Files:**
- 无文件改动，仅验证

- [ ] **Step 1: 运行全部相关测试**

Run: `pytest tests/common/test_autostart.py tests/test_installer_config.py -v`
Expected: PASS（所有测试通过）

- [ ] **Step 2: 运行全量测试确认无回归**

Run: `pytest`
Expected: PASS（无回归失败；与自启无关的既有失败若存在，记录但不阻断）

- [ ] **Step 3: 代码检查全量**

Run: `ruff check .`
Expected: 无错误

- [ ] **Step 4: 手动验证清单（在 Windows 打包环境）**

以下为人工验证项，无法自动化。打包后执行：

1. 全新安装、勾选"开机自动启动" → 检查 `HKLM\...\Run` 有 `test-worker` 值指向 exe
2. 全新安装、取消勾选 → 检查注册表无该值
3. 升级安装（覆盖已有版本）→ 注册表自启值保持升级前状态
4. 卸载 → 注册表 `test-worker` 值被清除
5. 运行时设置窗口打开 → checkbox 状态与 db `auto_start` 一致
6. 设置窗口勾选并保存 → db `auto_start=true`，注册表有值
7. 设置窗口取消勾选并保存 → db `auto_start=false`，注册表无值
8. 重启电脑 → 自启状态与 db 一致（勾上则启动，未勾则不启动）

- [ ] **Step 5: 最终提交（如有验证中发现的修复）**

若手动验证发现问题，修复后提交；若全部通过，本步骤无操作。
