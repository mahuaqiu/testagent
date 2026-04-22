"""
PyInstaller Runtime Hook: 隐藏所有 subprocess 调用的 CMD 窗口。

Windows 上打包后的 GUI 程序，如果第三方库内部调用 subprocess.run/Popen
但没有添加 CREATE_NO_WINDOW 标志，会弹出黑色 CMD 窗口。

此 hook 通过 monkey-patch 强制为所有 subprocess 调用添加隐藏窗口标志。
"""

import subprocess
import sys

# Windows 上才需要此 hook
if sys.platform == "win32":
    # CREATE_NO_WINDOW 标志：隐藏子进程的控制台窗口
    CREATE_NO_WINDOW = 0x08000000

    # 保存原始函数
    _original_run = subprocess.run
    _original_popen = subprocess.Popen

    def _patched_run(*args, **kwargs):
        """Patched subprocess.run with CREATE_NO_WINDOW."""
        # 添加隐藏窗口标志（不覆盖用户显式设置的值）
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        else:
            # 合并标志
            kwargs["creationflags"] |= CREATE_NO_WINDOW
        return _original_run(*args, **kwargs)

    def _patched_popen(*args, **kwargs):
        """Patched subprocess.Popen with CREATE_NO_WINDOW."""
        # 添加隐藏窗口标志（不覆盖用户显式设置的值）
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        else:
            # 合并标志
            kwargs["creationflags"] |= CREATE_NO_WINDOW
        return _original_popen(*args, **kwargs)

    # 替换 subprocess 函数
    subprocess.run = _patched_run
    subprocess.Popen = _patched_popen
