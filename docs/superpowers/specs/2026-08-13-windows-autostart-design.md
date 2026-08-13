# 开机自动启动功能设计

日期：2026-08-13

## 背景与目标

Worker（Test Worker）作为多端自动化测试执行基建，部署到测试机/会场机后通常希望常驻运行。
当前安装器（NSIS）和运行时（PyQt5 托盘 + 设置窗口）均不提供"开机自动启动"能力。

本设计在**不增加新依赖**的前提下，新增开机自启功能，覆盖两条入口：

1. **安装器**：安装时提供"开机自动启动"勾选项，默认勾上；勾选则在系统注册表写入开机自启键。
2. **运行时设置窗口**：托盘"设置"菜单呼出的设置窗口中，提供"开机自启"开关，用户可随时开启/关闭。

## 需求确认

| 编号 | 需求 | 说明 |
|------|------|------|
| R1 | 安装器配置页加"开机自动启动"checkbox，默认勾上 | 复用现有设备发现 checkbox 范式 |
| R2 | 全新安装勾选时写 `HKLM\...\Run` 注册表键（系统级） | 任何用户登录都生效 |
| R3 | 注册表写入失败不中断安装 | 自启是锦上添花，不得影响安装主流程 |
| R4 | 升级安装时跳过自启处理 | 既不写也不删，保留用户上次的状态 |
| R5 | 卸载时删除注册表键 | 否则开机启动已不存在的 exe 会报错 |
| R6 | 自启状态存 worker.db，不读 worker.yaml | db 的 `schema_meta` 为唯一真相源 |
| R7 | 程序首次启动读一次注册表播种到 db | 之后运行只读 db，不读注册表 |
| R8 | 设置窗口加"开机自启"开关 | 读 db 显示状态，保存时写 db 并同步注册表 |
| R9 | UAC 关闭，不考虑提权问题 | 注册表写 HKLM 不做权限处理 |
| R10 | 不增加新依赖 | 注册表用 Python 内置 `winreg` |

## 实现方案

方案对比后选定 **方案 A：注册表 Run 键 + 纯 Python 注册表工具模块**。

- **自启机制**：`HKLM\Software\Microsoft\Windows\CurrentVersion\Run` 下写值 `test-worker` = `"<exe全路径>"`
- **Python 侧**：新增 `common/autostart.py` 模块，封装 db 读写、注册表读写、播种、同步
- **设置窗口**：加 checkbox，委托 autostart 模块读写
- **db 播种**：Worker 启动后由程序读注册表初始化 db

放弃的备选方案：
- **启动文件夹快捷方式**：ProgramData 启动文件夹行为不如 Run 键稳定，生成 .lnk 需要 COM 调用更啰嗦。
- **任务计划程序（Task Scheduler）**：复杂度高一个量级，创建/删除/查询都要走 schtasks 命令行，出错面大。

## 整体架构

db 的 `schema_meta.auto_start` 是**唯一真相源**，注册表是它的投影：

```
┌─────────────────────────────────────────────────────────────┐
│  worker.db → schema_meta 表                                  │
│    key='auto_start', value='true'/'false'   ← 唯一真相源     │
└─────────────────────────────────────────────────────────────┘
            ▲                          ▲
            │ 读/写                    │ 读/写
            │                          │
┌───────────┴──────────┐   ┌──────────┴──────────────────────┐
│ 安装器 (NSIS)         │   │ 运行时 Python                    │
│ - 全新安装勾选→写注册表│   │ - 首次启动: 读注册表→播种 db      │
│ - 升级安装: 跳过       │   │ - 设置窗口: 读db显示, 保存时写db  │
│ - 卸载: 删注册表键      │   │   并同步注册表                    │
└───────────────────────┘   │ - 卸载由NSIS负责删注册表          │
                            └─────────────────────────────────┘
                                      ▲
                                      │ 投影/同步
                            ┌─────────┴─────────────────┐
                            │ HKLM\...\Run             │
                            │   test-worker = exe全路径 │
                            └───────────────────────────┘
```

### 状态模型

- **真相源**：`schema_meta` 表中 `key='auto_start'`，`value='true'` 或 `'false'`
- **投影**：`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run` 下 `test-worker` 值 = `sys.executable` 全路径（带引号，防路径含空格）
- **播种时机**：`GUIApp.run()` 里 Worker 启动后、托盘启动前，调一次 `seed_from_registry()`。db 无该 key 时读注册表有无 `test-worker` 值来初始化；db 已有则跳过（幂等）
- **设置窗口同步**：`set_enabled` 每次调用都先写 db 再同步注册表（false→true 写键，true→false 删键），不做变化检测——注册表写/删操作幂等，重复执行无副作用，实现更简单

### 三个组件的职责边界

| 组件 | 职责 | 不做的事 |
|------|------|----------|
| 安装器 (NSIS) | 全新安装勾选→写注册表；卸载→删注册表 | 不碰 db（无 SQLite 能力） |
| `common/autostart.py`（新模块） | db 读写、注册表读写、播种、同步 | 不碰 UI、不碰配置文件 |
| 设置窗口 | checkbox 展示与保存 | 不直接操作注册表/db，全部委托给 autostart 模块 |

安装器（NSIS）和设置窗口都只是 autostart 模块的调用方/对等方。设置窗口不背注册表细节，NSIS 不背 db 细节，各自只做自己擅长的。

## 组件设计

### 组件 1：`common/autostart.py`（新增）

所有自启逻辑收口在此。对调用方暴露最小接口：

```python
# 注册表 Run 键路径（系统级，所有用户登录生效）
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "test-worker"

def is_enabled() -> bool:
    """读 db 的 schema_meta.auto_start，返回当前自启意愿。
    db 无该 key 时返回 False（未初始化视为关闭）。"""

def seed_from_registry() -> None:
    """首次启动播种：db 无 auto_start key 时，读注册表有无 test-worker
    值来初始化 db。已有则跳过。幂等、不抛异常（失败只记日志）。"""

def set_enabled(enabled: bool) -> None:
    """设置窗口保存时调用。先写 db，再同步注册表。
    enabled=True→写 HKLM Run 键；enabled=False→删 Run 键。
    注册表操作失败不抛异常（只记日志），db 已更新成功即视为设置成功。"""
```

#### 内部实现要点

**db 访问**：模块不持有 db 句柄，每次操作用 `Database(get_base_dir() / "data" / "worker.db")` 临时建短连接读写 `schema_meta`。原因——db 句柄由 `WorkerRuntime` 持有且线程局部，自启模块被设置窗口（Qt 主线程）和启动流程分别调用，独立建短连接更简单；`schema_meta` 是低频读写，性能不是问题。

**注册表操作**：用 `winreg`（Python 内置，无新依赖）：
- 写：`winreg.CreateKeyEx(HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE)` → `SetValueEx(VALUE_NAME, 0, REG_SZ, f'"{exe_path}"')`
- 删：`winreg.OpenKey(HKEY_LOCAL_MACHINE, RUN_KEY, 0, winreg.KEY_SET_VALUE)` → `DeleteValue(VALUE_NAME)`，键不存在视为成功
- exe 路径：`sys.executable`（打包后即 `test-worker.exe` 全路径）

**错误处理统一原则**：所有注册表操作、db 操作都包 `try/except`，失败只 `logger.warning`，**绝不抛异常**。贯彻"自启是锦上添花、不能影响主流程"：
- 播种失败 → db 无 key，设置窗口显示"未勾选"，用户可手动开启
- 设置窗口同步注册表失败 → db 已更新，下次开机可能不自启，但设置已保存、程序不崩
- 非 Windows 平台（Mac）→ `winreg` 不可用，模块函数直接 no-op 返回 False

#### 非 Windows 平台安全降级

```python
import sys
if sys.platform == "win32":
    import winreg
else:
    winreg = None  # Mac 打包不在此需求范围，模块仍可安全 import
```

所有注册表操作内部判断 `winreg is None` 则 no-op。`is_enabled()` 在非 Windows 返回 False。Mac 打包脚本 `build_mac.sh` 不涉及自启，不受影响。

### 组件 2：安装器改动（`installer/installer.nsi`）

复用现有配置页 checkbox 范式（与 `DiscoverAndroid` 等完全同构）。

#### 改动清单

**1. 新增变量声明**（变量区）：
```nsis
Var AutoStart
```

**2. 配置页加 checkbox**（`ConfigPageCreate` 函数，设备发现那行之后）：
```nsis
; Row 6: 开机自动启动
${NSD_CreateCheckbox} 0 200 140 12u "开机自动启动"
Pop $AutoStart
```

**3. `.onInit` 设默认值**（与现有 `Discover*` 同模式）：
```nsis
StrCpy $AutoStart ${BST_CHECKED}   ; 默认勾上
```
不做命令行参数覆盖（需求确认去掉 `/AUTOSTART=` 参数）。

**4. `ConfigPageLeave` 读取勾选状态**：
```nsis
${NSD_GetState} $AutoStart $AutoStart
```
升级安装走 `skip_page`，此段被跳过，`$AutoStart` 保持默认值但安装段会判断升级跳过。

**5. 安装段写注册表**（`Section "MainSection" SEC01`，创建快捷方式之后）：
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

关键点：
- **升级跳过**：`StrCmp $IsUpgrade "1" skip_autostart` 一开头就跳过整个自启块。
- **写失败不中断**：`ClearErrors` / `IfErrors` 捕获失败，只 `DetailPrint` 警告，不 `SetErrors`、不 `Abort`，安装继续。
- **值带引号**：`'"$INSTDIR\test-worker.exe"'`——NSIS 字符串里内嵌一对双引号，防路径含空格（`Program Files` 必然有空格）。

**6. 卸载段删注册表键**（`Section Uninstall`，删快捷方式附近）：
```nsis
; 清理开机自启注册表项
DeleteRegValue HKLM "Software\Microsoft\Windows\CurrentVersion\Run" "test-worker"
```
`DeleteRegValue` 对不存在的值不报错，但仍保持一致性。

#### 升级安装行为

升级安装（`IsUpgrade=1`）时：
- 配置页整页跳过（现有逻辑）→ checkbox 不会被用户操作
- 安装段 `skip_autostart` → 既不写也不删注册表
- 结果：注册表 Run 键保持用户上一次全新安装时的状态

符合预期——升级不动自启，用户上次装时勾了就继续勾着，没勾就继续没有。

### 组件 3：设置窗口改动（`worker/settings_window.py`）

#### UI：加 checkbox

在 `_setup_ui` 设备发现那一行之后，加一条分隔线 + 自启开关：
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

#### 加载：`_load_values` 读 db

在设备发现那段之后：
```python
# 开机自启
from common.autostart import is_enabled
self.autostart_checkbox.setChecked(is_enabled())
```

#### 保存：`_on_save` 写 db + 同步注册表

**决策（方案 a）**：自启跟随"保存并重启"统一保存，而非独立即时生效。

理由：用户打开设置窗口通常还会改别的配置项（IP、端口等），"保存并重启"按钮语义本来就是"保存所有改动并重启"。自启作为其中一项一起保存，行为最一致、实现最简。自启保存本身是即时生效的（db + 注册表同步完成），重启只是顺带把其它配置项应用。

实现：在 `_on_save` 最前面（写 worker.yaml 之前）调用，独立于配置文件写入逻辑。这样即使配置文件写入走 fallback 分支，自启也能正常保存：
```python
def _on_save(self):
    if not self._validate():
        return

    # 开机自启：写 db + 同步注册表（独立于 worker.yaml）
    try:
        from common.autostart import set_enabled
        set_enabled(self.autostart_checkbox.isChecked())
    except Exception as e:
        logger.warning(f"保存开机自启设置失败: {e}")
        # 不阻断，继续保存其它配置

    # 以下为现有 worker.yaml 写入逻辑（原样不动）
    ...
```

自启保存失败只 `warning`，不弹错、不阻断后续配置保存——贯彻"自启失败不影响主流程"。

### 组件 4：启动播种改动（`worker/gui_main.py`）

在 `GUIApp.run()` 里，Worker 启动后、托盘启动前加一行：
```python
# 启动 Worker 服务
self._splash.update_status("启动 Worker 服务...")
self.app.processEvents()
self._start_worker()

# 播种开机自启状态到 db（首次启动读注册表初始化，幂等）
try:
    from common.autostart import seed_from_registry
    seed_from_registry()
except Exception as e:
    logger.warning(f"播种开机自启状态失败: {e}")

# 启动托盘
self._splash.update_status("启动系统托盘...")
...
```

- **放在 Worker 启动后**：此时 `data/worker.db` 已由 `WorkerRuntime.start()` 初始化（`Database.initialize` 建表），`schema_meta` 表已存在，播种可安全读写。
- **放 GUI 入口不放 `worker/main.py`**：打包后实际入口是 `gui_main.py`（Nuitka `--output-filename=test-worker.exe` 指向 `gui_main.py`），`main.py` 是非 GUI 的旧入口、打包不使用。播种放 GUI 入口才能覆盖打包场景。

## 数据流

### 全新安装（勾选自启）

```
安装器配置页: checkbox 默认勾上
  → ConfigPageLeave: $AutoStart = BST_CHECKED
  → 安装段(非升级): WriteRegStr HKLM Run test-worker="...\test-worker.exe"
程序首次启动:
  → seed_from_registry(): db 无 auto_start key, 读注册表有 test-worker 值
  → db 写 auto_start=true
设置窗口打开:
  → is_enabled() 读 db = true → checkbox 勾上
重启电脑:
  → 系统读 HKLM Run → 启动 test-worker.exe
```

### 设置窗口取消自启

```
设置窗口: checkbox 取消勾选 → 点"保存并重启"
  → set_enabled(False): db 写 auto_start=false, 删 HKLM Run test-worker 值
  → self.accept() → Worker 重启
重启电脑:
  → HKLM Run 无 test-worker → 不启动
```

### 升级安装

```
安装器检测: IsUpgrade=1
  → 配置页整页跳过(checkbox 不展示)
  → 安装段 skip_autostart(不写不删)
  → 注册表 Run 键保持上次状态
程序启动:
  → seed_from_registry(): db 已有 auto_start key(上次播种过) → 跳过
设置窗口:
  → is_enabled() 读 db(上次状态) → 正确显示
```

### 卸载

```
卸载段: DeleteRegValue HKLM Run test-worker
  → 注册表自启键清除
  → 开机不再尝试启动(即使 db 仍残留 auto_start, exe 已删, 无影响)
```

## 错误处理

| 场景 | 处理 |
|------|------|
| 安装器写注册表失败 | `ClearErrors`+`DetailPrint` 警告，安装继续 |
| 播种时读注册表失败 | `warning` 日志，db 无 key，设置窗口显示未勾选 |
| 播种时写 db 失败 | `warning` 日志，下次启动重试 |
| 设置窗口写 db 失败 | `warning` 日志，不阻断其它配置保存 |
| 设置窗口同步注册表失败 | `warning` 日志，db 已更新视为成功 |
| 非 Windows 平台 | `winreg` 为 None，所有操作 no-op，`is_enabled()` 返回 False |
| 卸载时注册表键不存在 | `DeleteRegValue` 不报错，静默通过 |

## 测试要点

- 全新安装勾选 → 重启后程序自动启动
- 全新安装不勾选 → 重启后不启动
- 升级安装 → 自启状态与升级前一致（不改变）
- 设置窗口开启自启 → 重启后启动
- 设置窗口关闭自启 → 重启后不启动
- 卸载 → 注册表 Run 键被清除
- 模拟注册表写入失败（权限/键被锁）→ 安装不中断，程序不崩
- db 播种幂等：多次启动结果一致

## 不在范围内

- Mac 平台开机自启（`build_mac.sh` 不涉及）
- 自启失败重试机制（失败只记日志，用户可在设置窗口手动重试）
- 自启状态的远程查询/控制（无 HTTP API 暴露自启状态）
