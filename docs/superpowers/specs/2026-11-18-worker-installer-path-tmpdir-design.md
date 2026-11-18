# Worker 安装器 PATH 选项与安装目录去空格设计

日期：2026-11-18（对齐后简化记录）

## 背景

1. HOScrcpy SDK（`hosScrcpy-1.0.18-beta.jar`，`com/huawei/hosscrcpy/api/b.class`）用
   `String.format` 拼出单条字符串 `hdc -s %s:%s -t %s file send %s %s` 再按空格 split 执行。
   本地文件来自 `File.createTempFile`（落在 `java.io.tmpdir`）。安装目录带空格时
   （默认 `C:\Program Files\Test Worker`），鸿蒙 PC 首次连接上传 agent 必失败。
2. 新装需要将 ADB/HDC 加入系统环境变量（PATH），安装界面加可选项，默认勾选。

## 改动点

### installer/installer.nsi

- `InstallDir` 由 `$PROGRAMFILES64\${PRODUCT_NAME}` 改为 `C:\TestWorker`（无空格）。
  升级兼容不动：`InstallDirRegKey` + 升级链路显式 `/D=当前目录`，存量设备（含
  `D:\code\Test Worker` 这类带空格目录）继续装原目录。
- 选项页新增勾选框「将 ADB/HDC 添加到系统环境变量」，默认勾选；仅全新安装生效
  （升级安装跳过，与开机自启同模式）；静默安装取默认值。
- 写入方式：PowerShell 直调注册表 API——`GetValue` 用 `DoNotExpandEnvironmentNames`
  读原始值，按 `;` 精确（大小写无关）匹配去重后追加 `$INSTDIR\tools\adb`、
  `$INSTDIR\tools\hdc`，`SetValue(..., ExpandString)` 写回，不破坏已有 REG_EXPAND_SZ 条目；
  之后 `SendMessage HWND_BROADCAST WM_SETTINGCHANGE "Environment"` 广播即时生效。
- 卸载时从系统 PATH 移除上述两个条目（同样保类型），并再次广播。
- 安装段无条件 `CreateDirectory "$COMMONPROGRAMDATA\TestWorker"`（升级也会执行），
  供带空格目录的存量设备做 java tmp 兜底；卸载时 `RMDir /r` 清理。

### worker/platforms/harmony_official/session.py

- 新增 `resolve_java_temp_root(base_dir)`：`<base>/data/harmony_official` 含空格
  （且 Windows）时兜底到 `C:\ProgramData\TestWorker\data\harmony_official`，
  否则维持安装目录内路径，行为不变。`_settings_for_serial` 改用它。
- `bridge.py` 已有 `temp_dir.mkdir(parents=True, exist_ok=True)`，兜底目录自动创建。

## 明确不动

- hdc 子进程 TMP/TEMP 重定向（`logs/hdc`）：hdc.exe 由 Python 列表参数启动，不受空格影响。
- `-Djava.io.tmpdir=` 与 classpath 传参：argv 逐元素传参，无 shell 拆分。
- HOScrcpy JAR 本身不修改。

## 场景矩阵

| 场景 | 安装目录 | 系统 PATH | HOScrcpy 首次上传 |
|---|---|---|---|
| 全新安装 | `C:\TestWorker` | 加入（默认勾选） | tmpdir 无空格，成功 |
| 手动选 `D:\TestWorker` | 同左 | 加入 | 安装目录内 tmpdir，成功 |
| 升级（存量带空格目录） | 原目录不变 | 不加 | tmpdir 兜底 ProgramData，成功 |
| 升级（存量无空格） | 原目录 | 不加 | 行为不变 |
| 卸载 | — | 移除条目 | — |

## 验证

- 单测：`resolve_java_temp_root` 空格/无空格断言（`tests/test_harmony_official.py`）。
- 手动：新装勾选/不勾 PATH、重装幂等、带空格目录升级后鸿蒙 PC 首连、卸载清理。
- `makensis` 本地编译通过。
