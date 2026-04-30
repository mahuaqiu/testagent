"""
打包环境检测工具模块。

支持 Nuitka 和 PyInstaller 两种打包方式。
"""

import sys
import os


def is_packaged():
    """检测是否在打包环境中（支持 Nuitka 和 PyInstaller）。"""
    # Nuitka 打包后会设置 __compiled__ 属性
    if hasattr(sys, '__compiled__'):
        return True
    # PyInstaller 打包后会设置 sys.frozen
    if getattr(sys, 'frozen', False):
        return True
    # Nuitka standalone 模式：检查 sys.executable 是否在包含 python.exe 的打包目录中
    # 打包后目录结构为：test-worker/python.exe, test-worker/test-worker.exe
    exe_dir = os.path.dirname(sys.executable)
    exe_name = os.path.basename(sys.executable).lower()
    # 如果 sys.executable 是 python.exe，且目录中存在主程序 exe，则认为是打包环境
    if exe_name == 'python.exe':
        # 检查目录中是否有非 python 的 exe 文件（主程序）
        try:
            for f in os.listdir(exe_dir):
                if f.lower().endswith('.exe') and f.lower() != 'python.exe':
                    return True
        except OSError:
            pass
    return False


def get_app_dir():
    """获取应用目录（兼容 Nuitka 和 PyInstaller）。"""
    if is_packaged():
        return os.path.dirname(sys.executable)
    else:
        return os.getcwd()


def get_base_dir():
    """获取项目根目录（打包环境为 exe 所在目录，开发环境为项目根目录）。"""
    if is_packaged():
        return os.path.dirname(sys.executable)
    else:
        # 开发环境：从当前文件向上两级到项目根目录
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
